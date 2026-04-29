import os, logging
from typing import Any
from google.cloud import secretmanager
from datetime import timedelta

logger = logging.getLogger(__name__)

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")

MEMBERSHIP_DURATIONS = {
    "weekend warrior": timedelta(days=2),
    "1 week pass": timedelta(weeks=1),
    "2 week pass": timedelta(weeks=2),
    "3 week pass": timedelta(weeks=3),
    "best rate!!! one year (pif)": timedelta(days=365),
    "day pass (not a class) - 4am-10pm for one individual, for one calendar day.": timedelta(days=0),
    "day pass": timedelta(days=0)
}


class Config:
    _secrets = {}

    @classmethod
    def get(cls, key: str) -> str | None:
        if key in cls._secrets:
            return cls._secrets[key]
        try:
            val = get_secret(key)
            cls._secrets[key] = val
            return val
        except Exception as e:
            logger.error(f"Failed to fetch config key '{key}': {e}")
            return None


def get_secret(secret_id: str, version_id: str = "latest") -> str:
    """Fetches a secret from Google Secret Manager."""
    if not GCP_PROJECT_ID:
        raise ValueError("GCP_PROJECT_ID environment variable not set.")

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{GCP_PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        logger.error(f"Error accessing secret '{secret_id}': {e}")
        raise e
