from google.cloud import secretmanager
from app import DeveloperPhoneNumber
from datetime import timedelta, datetime
from services import send_sms
import os, requests, time


GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")

class Config:
    _secrets = {}

    @classmethod
    def get(cls, key):
        if key in cls._secrets:
            return cls._secrets[key]
        try:
            val = get_secret(key)
            cls._secrets[key] = val
            return val
        except Exception as e:
            print(f"Failed to fetch config key {key}: {e}")
            return None


def get_secret(secret_id, version_id="latest"):
    """Fetches a secret from Google Secret Manager."""
    if not GCP_PROJECT_ID:
        raise ValueError("GCP_PROJECT_ID environment variable not set.")
        
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{GCP_PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        print(f"Error accessing secret: {secret_id}. Details: {e}")
        raise e


def get_vagaro_customer_details(cust_id):
    token = get_vagaro_token()
    if not token:
        return None

    business_id = Config.get("BUSINESS_ID")
    if not business_id:
        print("Missing Business ID")
        return None

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "accessToken": token,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    payload = {"businessId": business_id, "customerId": cust_id}
    
    try:
        vagaro_resp = requests.post(
            "https://api.vagaro.com/us03/api/v2/customers",
            json=payload,
            headers=headers
        )
        if vagaro_resp.status_code != 200:
            print(f"Vagaro customer API returned status {vagaro_resp.status_code} with response: {vagaro_resp.text}")
        vagaro_resp.raise_for_status()
        return vagaro_resp.json().get("data")
    
    except requests.exceptions.RequestException as e:
        send_sms(DeveloperPhoneNumber, f"Vagaro API error for customer {cust_id}: {e.response.text if e.response else e}")
        return None
    

def get_remotelock_token():
    global remote_lock_token, token_expiry
    if remote_lock_token and token_expiry and datetime.utcnow() < token_expiry:
        return remote_lock_token

    client_id = Config.get("REMOTELOCK_CLIENT_ID")
    client_secret = Config.get("REMOTELOCK_CLIENT_SECRET")
    token_url = Config.get("TOKEN_URL")

    if not all([client_id, client_secret, token_url]):
        return None

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }
    try:
        resp = requests.post(token_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        remote_lock_token = data["access_token"]
        token_expiry = datetime.utcnow() + timedelta(seconds=data["expires_in"] - 60)
        return remote_lock_token
    except requests.exceptions.RequestException as e:
        print(f"Error getting RemoteLock token: {e}")
        send_sms(DeveloperPhoneNumber, f"Could not refresh RemoteLock token: {e}")
        return None


def get_vagaro_token():
    global _vagaro_cached_token, _vagaro_expires_at
    now = time.time()
    if _vagaro_cached_token and now < _vagaro_expires_at - 60:
        return _vagaro_cached_token
        
    client_id = Config.get("VAGARO_CLIENT_ID")
    client_secret = Config.get("VAGARO_CLIENT_SECRET")

    if not all([client_id, client_secret]):
        return None

    url = "https://api.vagaro.com/us03/api/v2/merchants/generate-access-token"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    payload = {
        "clientId": client_id,
        "clientSecretKey": client_secret
    }
    
    try:
        r = requests.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()["data"]
        _vagaro_cached_token = data["access_token"]
        _vagaro_expires_at = now + data.get("expires_in", 3600)
        return _vagaro_cached_token
    
    except requests.exceptions.RequestException as e:
        print(f"Error getting Vagaro token: {e}")
        send_sms(DeveloperPhoneNumber, f"Could not refresh Vagaro token: {e.response.text if e.response else e}")
        return None