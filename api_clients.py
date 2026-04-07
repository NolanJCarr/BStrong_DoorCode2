import requests, time, json
from datetime import datetime, timedelta, timezone
from config import Config, send_Dev

remote_lock_token = None
token_expiry = datetime.min.replace(tzinfo=timezone.utc)
_vagaro_cached_token = None
_vagaro_expires_at = 0

REMOTELOCK_TOKEN_URL = "https://connect.remotelock.com/oauth/token"
BUSINESS_ID = "e9S4DjyPbv-ccrPDDqzBEA=="
WORKER_URL = "https://bstrong-vagaro-proxy.nolantatum6.workers.dev"


def get_vagaro_customer_details(cust_id):
    token = get_vagaro_token()
    if not token:
        print("Could not get customer details from Vagaro from either Missing token or Wrong BuisnessID")
        send_Dev("Could not get customer details from Vagaro from either Missing token or Wrong BuisnessID")
        return None

    headers = {
        "accessToken": token.strip(),
        "X-Target-Url": "https://api.vagaro.com/us03/api/v2/customers",
        "Content-Type": "application/json"
    }
    
    payload = {"businessId": BUSINESS_ID, "customerId": cust_id}
    
    try:
        vagaro_resp = requests.post(WORKER_URL, json=payload, headers=headers, timeout=10)
        vagaro_resp.raise_for_status()
        return vagaro_resp.json().get("data")
    except requests.exceptions.RequestException as e:
        error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
        send_Dev(f"STOP GUESSING. VAGARO SAID: {error_text}")
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
        print(f"Error getting RemoteLock token: {e}")
        send_Dev( f"Could not refresh RemoteLock token: {e}")
        return None


def get_vagaro_token():
    global _vagaro_cached_token, _vagaro_expires_at
    now = time.time()
    if _vagaro_cached_token and now < _vagaro_expires_at - 60:
        return _vagaro_cached_token

    headers = {
        "X-Target-Url": "https://api.vagaro.com/us03/api/v2/merchants/generate-access-token",
        "Content-Type": "application/json"
    }
    
    try:
        r = requests.post(WORKER_URL, json={}, headers=headers, timeout=10)
        r.raise_for_status()
        
        resp_json = r.json()
        data = resp_json.get("data", {})
        
        _vagaro_cached_token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)
        _vagaro_expires_at = now + expires_in
        
        return _vagaro_cached_token
        
    except requests.exceptions.RequestException as e:
        error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
        print(f"Error getting Vagaro token via Worker: {error_text}")
        send_Dev(f"Could not refresh Vagaro token: {error_text}")
        return None