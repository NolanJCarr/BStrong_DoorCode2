import pytz, logging
from typing import Any
from datetime import datetime, timedelta
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

logger = logging.getLogger(__name__)


class Database:
    def __init__(self):
        self.database = firestore.Client(database="bstrong2")

    def checkIfExists(self, collection: str, key: str) -> bool:
        reference = self.database.collection(collection).document(key)
        if reference.get().exists:
            logger.info(f"Duplicate transaction item ignored: {key}")
            return True
        else:
            return False

    def add(self, collection: str, key: str, data: dict[str, Any] | None = None) -> None:
        reference = self.database.collection(collection).document(key)
        if data:
            reference.set(data)
        else:
            reference.set({})

    def update(self, collection: str, key: str, data: dict[str, Any]) -> None:
        reference = self.database.collection(collection).document(key)
        reference.update(data)

    def getData(self, collection: str, key: str) -> Any:
        reference = self.database.collection(collection).document(key)
        return reference.get()

    def delete(self, collection: str, key: str) -> None:
        reference = self.database.collection(collection).document(key)
        reference.delete()

    def getAllOldDocs(self) -> list[Any]:
        two_days_ago = datetime.now(pytz.utc) - timedelta(days=2)
        filter_condition = FieldFilter('timestamp', '<', two_days_ago)

        docs_pending = self.database.collection('pending_customers').where(filter=filter_condition).get()
        docs_tickets = self.database.collection('pin_change_tickets').where(filter=filter_condition).get()
        docs_transactions = self.database.collection('processed_transactions').where(filter=filter_condition).get()
        return docs_pending + docs_tickets + docs_transactions

    def getBatch(self) -> Any:
        return self.database.batch()

    def getExpiredAutopays(self) -> list[Any]:
        now = datetime.now(pytz.utc)
        filter_condition = FieldFilter('expireAt', '<=', now)
        return self.database.collection('active_autopays').where(filter=filter_condition).get()
