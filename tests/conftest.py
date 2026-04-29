import pytest
from unittest.mock import patch, MagicMock

# ---- Test values ----
# Owner numbers are fake — texts to them are silently dropped.
# Developer number is real — send_Dev() will actually text you during test runs.
TEST_CONFIG = {
    'OWNER_PHONE_NUMBER_1': '+10000000001',   # fake, dropped
    'OWNER_PHONE_NUMBER_2': '+10000000002',   # fake, dropped
    'MISC_PERSON_CUSTID':   'MISC_TEST_ID',
    'DEVELOPER_PHONE_NUMBER': '+17745218808', # real — you will receive these
    'TRANSACTION_TOKEN': 'test-transaction-token',
    'FORUM_TOKEN':       'test-forum-token',
    'CLEANUP_TOKEN':     'test-cleanup-token',
    'LOCK_ID':           'test-lock-id',
    'REMOTELOCK_CLIENT_ID':     'test-rl-client-id',
    'REMOTELOCK_CLIENT_SECRET': 'test-rl-secret',
    # Twilio credentials are intentionally omitted here so that Config falls
    # through to the real Secret Manager, giving send_Dev() live credentials.
}

OWNER_NUMBERS = {TEST_CONFIG['OWNER_PHONE_NUMBER_1'], TEST_CONFIG['OWNER_PHONE_NUMBER_2']}
DEV_NUMBER    = TEST_CONFIG['DEVELOPER_PHONE_NUMBER']

# Pre-populate Config cache BEFORE importing app so module-level Config.get()
# calls (Owner1, Owner2, miscCustomerID) never reach Secret Manager.
from config import Config
Config._secrets.update(TEST_CONFIG)

# Patch Firestore before importing app to avoid real DB connections.
# Secret Manager is NOT patched so that Twilio credentials can be fetched
# from GCP and used by send_Dev() to actually text the developer.
_firestore_patcher = patch('google.cloud.firestore.Client')
_secretmanager_patcher = patch('google.cloud.secretmanager.SecretManagerServiceClient')
_firestore_patcher.start()
_secretmanager_patcher.start()

import app as flask_app


@pytest.fixture(autouse=True)
def selective_sms():
    """
    SMS routing during tests:
      - Owner numbers  → silently dropped (returns False, no real call)
      - Developer      → real Twilio call — you will receive these texts
      - Member phones  → mocked success (returns True, no real call)

    This ensures the gym owners are never spammed during test runs.
    """
    from utils import send_sms as _real_send_sms

    def guarded(to_phone_number, body, to_phone_number_2=None, **kwargs):
        if to_phone_number in OWNER_NUMBERS:
            return False
        if to_phone_number == DEV_NUMBER:
            return _real_send_sms(to_phone_number, body,
                                  to_phone_number_2=to_phone_number_2, **kwargs)
        # All other numbers (test member phones) — fake success, no real call
        return True

    with patch('utils.send_sms',    guarded), \
         patch('app.send_sms',      guarded), \
         patch('services.send_sms', guarded):
        yield


@pytest.fixture
def app_client(monkeypatch):
    """
    Flask test client with DB and API clients replaced by mocks.
    Yields (client, mock_db, mock_rl_client, mock_vagaro_client).
    """
    flask_app.app.config['TESTING'] = True
    monkeypatch.setattr(flask_app, 'Owner1',         TEST_CONFIG['OWNER_PHONE_NUMBER_1'])
    monkeypatch.setattr(flask_app, 'Owner2',         TEST_CONFIG['OWNER_PHONE_NUMBER_2'])
    monkeypatch.setattr(flask_app, 'miscCustomerID', TEST_CONFIG['MISC_PERSON_CUSTID'])

    mock_db = MagicMock()
    monkeypatch.setattr(flask_app, 'dataBase',      mock_db)

    mock_rl = MagicMock()
    monkeypatch.setattr(flask_app, 'rl_client',     mock_rl)

    mock_vagaro = MagicMock()
    monkeypatch.setattr(flask_app, 'vagaro_client', mock_vagaro)

    with flask_app.app.test_client() as client:
        yield client, mock_db, mock_rl, mock_vagaro


def make_firestore_doc(exists=True, data=None):
    """Return a mock Firestore document snapshot."""
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data or {}
    return doc
