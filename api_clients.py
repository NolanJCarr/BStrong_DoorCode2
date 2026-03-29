import requests, time, json
from datetime import datetime, timedelta, timezone
from config import Config, send_Dev

remote_lock_token = None
token_expiry = datetime.min.replace(tzinfo=timezone.utc)
_vagaro_cached_token = None
_vagaro_expires_at = 0

VAGARO_URL = "https://api.vagaro.com/us03/api/v2/customers"
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
        "Authorization": f"Bearer {token}",
        "X-Target-Url": f"https://api.vagaro.com/us03/api/v2/customers",
        "Content-Type": "application/json"
    }
    
    payload = {"businessId": BUSINESS_ID, "customerId": cust_id}
    
    try:
        vagaro_resp = requests.post(WORKER_URL, json=payload, headers=headers, timeout=10)
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
        print(f"Error getting RemoteLock token: {e}")
        send_Dev( f"Could not refresh RemoteLock token: {e}")
        return None


def get_vagaro_token():
    global _vagaro_cached_token, _vagaro_expires_at
    now = time.time()
    if _vagaro_cached_token and now < _vagaro_expires_at - 60:
        return _vagaro_cached_token
        
    client_id = Config.get("VAGARO_CLIENT_ID")
    client_secret = Config.get("VAGARO_CLIENT_SECRET")
    id_val = "U2FsdGVkX19Lq2o04YnVM8ShELlFW7op3bMwltpBCLI="
    secret_val = "ElRXpaiPsJzZSRepmZmwuNRbCXNvws"

    print(f"DEBUG: ID starts with: {str(client_id)[:4]}... Secret length: {len(str(client_secret))}")

    if not all([client_id, client_secret]):
        return None

    headers = {
        "X-Target-Url": "https://api.vagaro.com/us03/api/v2/merchants/generate-access-token",
        "Content-Type": "application/json"
    }
    payload = {
        "clientId": id_val,
        "clientSecretKey": secret_val
    }
    
    try:
        r = requests.post(WORKER_URL, json={}, headers=headers, timeout=10)
        r.raise_for_status()
        resp_json = r.json()
        
        # LOG THIS so you can see the structure in GCP Logs
        print(f"RAW WORKER RESPONSE: {resp_json}")

        # Vagaro usually returns { "data": { "access_token": "..." } }
        # But if your worker returns it differently, we handle both:
        if "data" in resp_json:
            _vagaro_cached_token = resp_json["data"].get("access_token")
        else:
            _vagaro_cached_token = resp_json.get("access_token")

        if not _vagaro_cached_token:
            raise ValueError("Token not found in response")

        _vagaro_expires_at = time.time() + 3600
        return _vagaro_cached_token
    
    except requests.exceptions.RequestException as e:
        print(f"Error getting Vagaro token via Worker: {e}")
        # Providing more detail in the dev alert helps troubleshoot
        error_msg = e.response.text if e.response else str(e)
        send_Dev(f"Could not refresh Vagaro token: {error_msg}")
        return None