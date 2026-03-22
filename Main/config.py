from google.cloud import secretmanager
from datetime import timedelta, datetime
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


def get_vagaro_customer_details(cust_id, database):
    token = get_vagaro_token(database)
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
        from services import send_sms
        send_sms(DeveloperPhoneNumber, f"Vagaro API error for customer {cust_id}: {e.response.text if e.response else e}")
        return None
    

def get_remotelock_token(db_helper):
    token_doc = db_helper.getData('system_auth', 'remotelock_token')
    now = time.time()

    if token_doc.exists:
        cached = token_doc.to_dict()
        if now < (cached.get('expires_at', 0) - 300):
            return cached.get('access_token')

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
        resp = requests.post(token_url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        new_token = data["access_token"]
        duration = data.get("expires_in", 3600) 
        db_helper.add('system_auth', 'remotelock_token', {
            'access_token': new_token,
            'expires_at': now + duration
        })
        print("Successfully refreshed and stored new RemoteLock token.")
        return new_token
    
    except requests.exceptions.RequestException as e:
        from services import send_sms
        DeveloperPhoneNumber = Config.get("DEVELOPER_PHONE_NUMBER")
        print(f"Error getting RemoteLock token: {e}")
        send_sms(DeveloperPhoneNumber, f"Could not refresh RemoteLock token: {e}")
        return None


def get_vagaro_token(db_helper):
    cached = db_helper.getData('system_auth', 'vagaro_token')
    now = time.time()

    if cached.exists:
        data = cached.to_dict()
        # Use a 5-minute (300s) safety buffer
        if now < (data.get('expires_at', 0) - 300):
            return data.get('access_token')
       
    url = "https://api.vagaro.com/us03/api/v2/merchants/generate-access-token"
    payload = {
        "clientId": Config.get("VAGARO_CLIENT_ID"),
        "clientSecretKey": Config.get("VAGARO_CLIENT_SECRET")
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        r = requests.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()["data"]
        vagaro_cached_token = data["access_token"]
        duration = data.get("expires_in", 3600)

        db_helper.add('system_auth', 'vagaro_token', {
            'access_token': vagaro_cached_token,
            'expires_at': now + duration
        })

        print("Successfully refreshed and stored new Vagaro token.")
        return vagaro_cached_token
    
    except requests.exceptions.RequestException as e:
        from services import send_sms
        DeveloperPhoneNumber = Config.get("DEVELOPER_PHONE_NUMBER")
        
        error_detail = e.response.text if hasattr(e, 'response') and e.response else str(e)
        print(f"Error getting Vagaro token: {error_detail}")
        
        if DeveloperPhoneNumber:
            send_sms(DeveloperPhoneNumber, f"Vagaro Token Refresh Failed: {error_detail[:100]}")
        return None