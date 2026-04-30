import requests, time, logging
from typing import Any
from datetime import datetime, timedelta, timezone
from .config import Config
from .utils import send_Dev

logger = logging.getLogger(__name__)

REMOTELOCK_BASE_URL = "https://api.remotelock.com"
REMOTELOCK_TOKEN_URL = "https://connect.remotelock.com/oauth/token"
VAGARO_WORKER_URL = "https://bstrong-vagaro-proxy.nolantatum6.workers.dev"
VAGARO_BUSINESS_ID = "e9S4DjyPbv-ccrPDDqzBEA=="
LOCK_SCHEDULE_ID = "d18e46f1-22b4-4880-9b0b-3d1ea60441fc"


class PinConflictError(Exception):
    """Raised when a RemoteLock PIN is already in use (HTTP 422)."""
    pass


class RemoteLockClient:
    def __init__(self):
        self._token = None
        self._token_expiry = datetime.min.replace(tzinfo=timezone.utc)

    def _get_token(self) -> str | None:
        now = datetime.now(timezone.utc)
        if self._token and now + timedelta(seconds=30) < self._token_expiry:
            return self._token

        client_id = Config.get("REMOTELOCK_CLIENT_ID")
        client_secret = Config.get("REMOTELOCK_CLIENT_SECRET")

        if not all([client_id, client_secret]):
            logger.error("Missing RemoteLock credentials (client_id or client_secret).")
            return None

        try:
            resp = requests.post(REMOTELOCK_TOKEN_URL, json={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret
            }, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            self._token = data["access_token"]
            self._token_expiry = now + timedelta(seconds=data.get("expires_in", 3600) - 60)
            logger.info(f"RemoteLock token refreshed. Expires at {self._token_expiry.isoformat()}")
            return self._token
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting RemoteLock token: {e}")
            send_Dev(f"Could not refresh RemoteLock token: {e}")
            return None

    def _headers(self) -> dict[str, str]:
        token = self._get_token()
        if not token:
            raise RuntimeError("Could not obtain RemoteLock access token.")
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.lockstate+json; version=1",
            "Content-Type": "application/json"
        }

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make a RemoteLock HTTP request, retrying once after 2s on network failure."""
        for attempt in range(2):
            try:
                return requests.request(method, url, **kwargs)
            except requests.exceptions.RequestException as e:
                if attempt == 1:
                    raise
                logger.warning(f"RemoteLock {method.upper()} failed (attempt 1), retrying in 2s: {e}")
                time.sleep(2)

    def create_access_person(self, name: str, starts_at: str, ends_at: str) -> tuple[str, str]:
        """Create a new access guest. Returns (guest_id, pin). Raises on failure."""
        resp = self._request_with_retry('POST', f"{REMOTELOCK_BASE_URL}/access_persons", json={
            "type": "access_guest",
            "attributes": {
                "name": name,
                "generate_pin": True,
                "starts_at": starts_at,
                "ends_at": ends_at
            }
        }, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        guest = resp.json()["data"]
        return guest["id"], guest["attributes"]["pin"]

    def grant_lock_access(self, guest_id: str, lock_id: str) -> None:
        """Grant a guest access to the configured lock. Raises on failure."""
        resp = self._request_with_retry(
            'POST', f"{REMOTELOCK_BASE_URL}/access_persons/{guest_id}/accesses",
            json={"attributes": {
                "accessible_id": lock_id,
                "accessible_type": "lock",
                "access_schedule_id": LOCK_SCHEDULE_ID
            }},
            headers=self._headers(),
            timeout=15
        )
        resp.raise_for_status()

    def update_pin(self, guest_id: str, pin: str) -> None:
        """Update a guest's PIN. Raises PinConflictError on 422, RequestException on other failures."""
        resp = self._request_with_retry(
            'PUT', f"{REMOTELOCK_BASE_URL}/access_persons/{guest_id}",
            json={"attributes": {"pin": pin}},
            headers=self._headers(),
            timeout=15
        )
        if resp.status_code == 422:
            raise PinConflictError(f"PIN {pin} is already in use.")
        resp.raise_for_status()

    def extend_access(self, guest_id: str, ends_at: str) -> None:
        """Extend a guest's access end time. Raises on failure."""
        resp = self._request_with_retry(
            'PUT', f"{REMOTELOCK_BASE_URL}/access_persons/{guest_id}",
            json={"attributes": {"ends_at": ends_at}},
            headers=self._headers(),
            timeout=15
        )
        resp.raise_for_status()


class VagaroClient:
    def __init__(self):
        self._token = None
        self._token_expiry = 0

    def _get_token(self) -> str | None:
        now = time.time()
        if self._token and now < self._token_expiry - 60:
            return self._token

        try:
            r = requests.post(VAGARO_WORKER_URL, json={}, headers={
                "X-Target-Url": "https://api.vagaro.com/us03/api/v2/merchants/generate-access-token",
                "Content-Type": "application/json"
            }, timeout=10)
            r.raise_for_status()

            data = r.json().get("data", {})
            self._token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            self._token_expiry = now + expires_in
            logger.info(f"Vagaro token refreshed. Expires in {expires_in}s.")
            return self._token

        except requests.exceptions.RequestException as e:
            error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
            logger.error(f"Error getting Vagaro token via Worker: {error_text}")
            send_Dev(f"Could not refresh Vagaro token: {error_text}")
            return None

    def get_customer_details(self, cust_id: str) -> dict[str, Any] | None:
        """Fetch customer details from Vagaro. Returns customer dict or None."""
        token = self._get_token()
        if not token:
            logger.error("Could not get Vagaro customer details: missing token or wrong BusinessID.")
            send_Dev("Could not get customer details from Vagaro from either Missing token or Wrong BuisnessID")
            return None

        try:
            resp = requests.post(VAGARO_WORKER_URL, json={
                "businessId": VAGARO_BUSINESS_ID,
                "customerId": cust_id
            }, headers={
                "accessToken": token.strip(),
                "X-Target-Url": "https://api.vagaro.com/us03/api/v2/customers",
                "Content-Type": "application/json"
            }, timeout=10)
            resp.raise_for_status()
            return resp.json().get("data")
        except requests.exceptions.RequestException as e:
            error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
            logger.error(f"Vagaro API error fetching customer {cust_id}: {error_text}")
            send_Dev(f"STOP GUESSING. VAGARO SAID: {error_text}")
            return None
