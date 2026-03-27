import requests, time
from datetime import datetime, timedelta, timezone
from config import Config, send_Dev

remote_lock_token = None
token_expiry = datetime.min.replace(tzinfo=timezone.utc)
_vagaro_cached_token = None
_vagaro_expires_at = 0

VAGARO_URL = "https://api.vagaro.com/us03/api/v2/customers"
VAGARO_TOKEN_URL = "https://api.vagaro.com/us03/api/v2/merchants/generate-access-token"
REMOTELOCK_TOKEN_URL = "https://connect.remotelock.com/oauth/token"
BUSINESS_ID = Config.get("BUSINESS_ID")


def get_vagaro_customer_details(cust_id):

    token = get_vagaro_token()
    if not token or not BUSINESS_ID:
        print("Could not get customer details from Vagaro from either Missing token or Wrong BuisnessID")
        send_Dev("Could not get customer details from Vagaro from either Missing token or Wrong BuisnessID")
        return None

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "accessToken": token,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    payload = {"businessId": BUSINESS_ID, "customerId": cust_id}
    
    try:
        vagaro_resp = requests.post(
            VAGARO_URL,
            json=payload,
            headers=headers,
            timeout=10
        )
        if vagaro_resp.status_code != 200:
            print(f"Vagaro customer API returned status {vagaro_resp.status_code} with response: {vagaro_resp.text}")
        vagaro_resp.raise_for_status()
        return vagaro_resp.json().get("data")
    
    except requests.exceptions.RequestException as e:
        send_Dev(f"Vagaro API error for customer {cust_id}: {e.response.text if e.response else e}")
        return None


def get_remotelock_token():
    global remote_lock_token, token_expiry
    now = datetime.now(timezone.utc)
    if remote_lock_token and token_expiry and now + timedelta(seconds=30) < token_expiry:
        return remote_lock_token

    client_id = Config.get("REMOTELOCK_CLIENT_ID")
    client_secret = Config.get("REMOTELOCK_CLIENT_SECRET")

    if not all([client_id, client_secret, REMOTELOCK_TOKEN_URL]):
        return None

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }
    try:
        resp = requests.post(REMOTELOCK_TOKEN_URL, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        remote_lock_token = data["access_token"]
        token_expiry = now + timedelta(seconds=data.get("expires_in", 3600) - 60)
        return remote_lock_token
    except requests.exceptions.RequestException as e:
        from services import send_sms
        DeveloperPhoneNumber = Config.get("DEVELOPER_PHONE_NUMBER")
        print(f"Error getting RemoteLock token: {e}")
        send_Dev( f"Could not refresh RemoteLock token: {e}")
        return None


def get_vagaro_token(db_helper):
    cached = db_helper.getData('system_auth', 'vagaro_token')
    now = time.time()
    if _vagaro_cached_token and now < _vagaro_expires_at - 60:
        return _vagaro_cached_token
        
    client_id = Config.get("VAGARO_CLIENT_ID")
    client_secret = Config.get("VAGARO_CLIENT_SECRET")

    if not all([client_id, client_secret]):
        return None


    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        r = requests.post(VAGARO_TOKEN_URL, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
        resp_json = r.json()
        data = resp_json.get("data", {})
        _vagaro_cached_token = data.get("access_token")
        _vagaro_expires_at = now + data.get("expires_in", 3600)
        return _vagaro_cached_token
    
    except requests.exceptions.RequestException as e:
        print(f"Error getting Vagaro token: {e}")
        send_Dev(f"Could not refresh Vagaro token: {e.response.text if e.response else e}")
        return None