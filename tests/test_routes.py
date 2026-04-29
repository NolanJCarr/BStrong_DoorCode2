import pytest
import pytz
import requests as req_lib
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from api_clients import PinConflictError
from tests.conftest import make_firestore_doc, TEST_CONFIG

TRANSACTION_TOKEN = TEST_CONFIG['TRANSACTION_TOKEN']
FORUM_TOKEN       = TEST_CONFIG['FORUM_TOKEN']
CLEANUP_TOKEN     = TEST_CONFIG['CLEANUP_TOKEN']
OWNER1            = TEST_CONFIG['OWNER_PHONE_NUMBER_1']
OWNER2            = TEST_CONFIG['OWNER_PHONE_NUMBER_2']

VALID_CUSTOMER = {
    'first_name':   'John',
    'last_name':    'Doe',
    'phone_number': '5085551234',
}


def transaction_payload(**overrides):
    payload = {
        'itemSold':      '1 month gym membership',
        'customerId':    'CUST123',
        'purchaseType':  'Membership',
        'userPaymentId': 'PAY123',
    }
    payload.update(overrides)
    return {'payload': payload}


# ---- /webhook-transaction -----------------------------------------------

class TestTransactionWebhook:
    def test_bad_signature_rejected(self, app_client):
        client, *_ = app_client
        resp = client.post('/webhook-transaction',
            json=transaction_payload(),
            headers={'X-Vagaro-Signature': 'wrong-token'})
        assert resp.status_code == 403

    def test_misc_customer_ignored(self, app_client):
        client, mock_db, *_ = app_client
        resp = client.post('/webhook-transaction',
            json=transaction_payload(customerId='MISC_TEST_ID'),
            headers={'X-Vagaro-Signature': TRANSACTION_TOKEN})
        assert resp.status_code == 200
        mock_db.checkIfExists.assert_not_called()

    def test_irrelevant_purchase_type_ignored(self, app_client):
        client, mock_db, *_ = app_client
        resp = client.post('/webhook-transaction',
            json=transaction_payload(purchaseType='Service'),
            headers={'X-Vagaro-Signature': TRANSACTION_TOKEN})
        assert resp.status_code == 200
        mock_db.checkIfExists.assert_not_called()

    def test_duplicate_transaction_skipped(self, app_client):
        client, mock_db, mock_rl, *_ = app_client
        mock_db.checkIfExists.return_value = True

        resp = client.post('/webhook-transaction',
            json=transaction_payload(),
            headers={'X-Vagaro-Signature': TRANSACTION_TOKEN})

        assert resp.status_code == 200
        assert b'Duplicate' in resp.data
        mock_rl.create_access_person.assert_not_called()

    def test_valid_purchase_firestore_path_success(self, app_client):
        client, mock_db, mock_rl, _ = app_client
        mock_db.checkIfExists.return_value = False
        mock_db.getData.return_value = make_firestore_doc(data=VALID_CUSTOMER)
        mock_rl.create_access_person.return_value = ('guest-123', '4567')

        resp = client.post('/webhook-transaction',
            json=transaction_payload(),
            headers={'X-Vagaro-Signature': TRANSACTION_TOKEN})

        assert resp.status_code == 200
        mock_rl.create_access_person.assert_called_once()
        mock_rl.grant_lock_access.assert_called_once()

    def test_valid_purchase_api_fallback_path(self, app_client):
        client, mock_db, mock_rl, mock_vagaro = app_client
        mock_db.checkIfExists.return_value = False
        mock_db.getData.return_value = make_firestore_doc(exists=False)
        mock_vagaro.get_customer_details.return_value = {
            'customerFirstName': 'Jane',
            'customerLastName':  'Smith',
            'mobilePhone':       '5085559876',
        }
        mock_rl.create_access_person.return_value = ('guest-456', '8901')

        resp = client.post('/webhook-transaction',
            json=transaction_payload(),
            headers={'X-Vagaro-Signature': TRANSACTION_TOKEN})

        assert resp.status_code == 200
        mock_vagaro.get_customer_details.assert_called_once_with('CUST123')

    def test_remotelock_failure_returns_500(self, app_client):
        client, mock_db, mock_rl, _ = app_client
        mock_db.checkIfExists.return_value = False
        mock_db.getData.return_value = make_firestore_doc(data=VALID_CUSTOMER)
        mock_rl.create_access_person.side_effect = req_lib.exceptions.RequestException("Timeout")

        resp = client.post('/webhook-transaction',
            json=transaction_payload(),
            headers={'X-Vagaro-Signature': TRANSACTION_TOKEN})

        assert resp.status_code == 500

    def test_day_pass_does_not_create_pin_ticket(self, app_client):
        client, mock_db, mock_rl, _ = app_client
        mock_db.checkIfExists.return_value = False
        mock_db.getData.return_value = make_firestore_doc(data=VALID_CUSTOMER)
        mock_rl.create_access_person.return_value = ('guest-789', '2345')

        resp = client.post('/webhook-transaction',
            json=transaction_payload(itemSold='day pass', purchaseType='Package'),
            headers={'X-Vagaro-Signature': TRANSACTION_TOKEN})

        assert resp.status_code == 200
        written_collections = [c[0][0] for c in mock_db.add.call_args_list]
        assert 'pin_change_tickets' not in written_collections

    def test_non_day_pass_creates_pin_ticket(self, app_client):
        client, mock_db, mock_rl, _ = app_client
        mock_db.checkIfExists.return_value = False
        mock_db.getData.return_value = make_firestore_doc(data=VALID_CUSTOMER)
        mock_rl.create_access_person.return_value = ('guest-mem', '6789')

        resp = client.post('/webhook-transaction',
            json=transaction_payload(),
            headers={'X-Vagaro-Signature': TRANSACTION_TOKEN})

        assert resp.status_code == 200
        written_collections = [c[0][0] for c in mock_db.add.call_args_list]
        assert 'pin_change_tickets' in written_collections

    def test_first_month_autopay_creates_new_code(self, app_client):
        client, mock_db, mock_rl, _ = app_client
        mock_db.checkIfExists.return_value = False
        # getData called twice: pending_customers then active_autopays (not found)
        mock_db.getData.side_effect = [
            make_firestore_doc(data=VALID_CUSTOMER),
            make_firestore_doc(exists=False),
        ]
        mock_rl.create_access_person.return_value = ('guest-auto', '3456')

        resp = client.post('/webhook-transaction',
            json=transaction_payload(itemSold='monthly autopay membership'),
            headers={'X-Vagaro-Signature': TRANSACTION_TOKEN})

        assert resp.status_code == 200
        mock_rl.create_access_person.assert_called_once()
        written_collections = [c[0][0] for c in mock_db.add.call_args_list]
        assert 'active_autopays'    in written_collections
        assert 'pin_change_tickets' in written_collections

    def test_autopay_extension_extends_existing_code(self, app_client):
        client, mock_db, mock_rl, _ = app_client
        mock_db.checkIfExists.return_value = False
        existing_expiry = pytz.utc.localize(datetime(2026, 3, 29, 22, 5))
        mock_db.getData.side_effect = [
            make_firestore_doc(data=VALID_CUSTOMER),
            make_firestore_doc(exists=True, data={
                'remote_lock_id': 'guest-existing',
                'expireAt':       existing_expiry,
            }),
        ]

        resp = client.post('/webhook-transaction',
            json=transaction_payload(itemSold='monthly autopay membership'),
            headers={'X-Vagaro-Signature': TRANSACTION_TOKEN})

        assert resp.status_code == 200
        mock_rl.extend_access.assert_called_once()
        mock_rl.create_access_person.assert_not_called()


# ---- /webhook-sms -------------------------------------------------------

class TestSMSPINWebhook:
    """Helper: post a fake inbound Twilio SMS with the validator mocked out."""
    def _sms(self, client, from_number, body):
        with patch('app.RequestValidator') as mock_val:
            mock_val.return_value.validate.return_value = True
            return client.post('/webhook-sms',
                data={'From': from_number, 'Body': body},
                content_type='application/x-www-form-urlencoded',
                headers={'X-Twilio-Signature': 'fake-sig'})

    def test_bad_twilio_signature_rejected(self, app_client):
        client, *_ = app_client
        with patch('app.RequestValidator') as mock_val:
            mock_val.return_value.validate.return_value = False
            resp = client.post('/webhook-sms',
                data={'From': '+15085551234', 'Body': '1234'},
                content_type='application/x-www-form-urlencoded')
        assert resp.status_code == 403

    def test_no_ticket_ignored(self, app_client):
        client, mock_db, *_ = app_client
        mock_db.getData.return_value = make_firestore_doc(exists=False)

        resp = self._sms(client, '+15085551234', '1234')

        assert resp.status_code == 200
        assert b'No ticket' in resp.data

    def test_expired_ticket_rejected(self, app_client):
        client, mock_db, *_ = app_client
        old_ts = pytz.utc.localize(datetime(2026, 1, 1, 0, 0))
        mock_db.getData.return_value = make_firestore_doc(data={
            'remote_lock_id': 'guest-123',
            'timestamp':      old_ts,
        })

        resp = self._sms(client, '+15085551234', '1234')

        assert resp.status_code == 200
        assert b'expired' in resp.data.lower()

    def test_non_numeric_pin_rejected(self, app_client):
        client, mock_db, *_ = app_client
        mock_db.getData.return_value = make_firestore_doc(data={
            'remote_lock_id': 'guest-123',
            'timestamp':      datetime.now(timezone.utc),
        })

        resp = self._sms(client, '+15085551234', 'abc')

        assert resp.status_code == 200
        assert b'Invalid' in resp.data

    def test_too_short_pin_rejected(self, app_client):
        client, mock_db, *_ = app_client
        mock_db.getData.return_value = make_firestore_doc(data={
            'remote_lock_id': 'guest-123',
            'timestamp':      datetime.now(timezone.utc),
        })

        resp = self._sms(client, '+15085551234', '123')

        assert resp.status_code == 200
        assert b'Invalid' in resp.data

    def test_hash_suffix_stripped_before_validation(self, app_client):
        client, mock_db, mock_rl, *_ = app_client
        mock_db.getData.return_value = make_firestore_doc(data={
            'remote_lock_id': 'guest-123',
            'timestamp':      datetime.now(timezone.utc),
        })

        self._sms(client, '+15085551234', '7890#')

        mock_rl.update_pin.assert_called_once_with('guest-123', '7890')

    def test_successful_pin_change(self, app_client):
        client, mock_db, mock_rl, *_ = app_client
        mock_db.getData.return_value = make_firestore_doc(data={
            'remote_lock_id': 'guest-123',
            'timestamp':      datetime.now(timezone.utc),
        })

        resp = self._sms(client, '+15085551234', '7890')

        assert resp.status_code == 200
        assert b'PIN updated' in resp.data
        mock_rl.update_pin.assert_called_once_with('guest-123', '7890')
        mock_db.delete.assert_called_once_with('pin_change_tickets', '+15085551234')

    def test_pin_conflict_returns_200_with_retry_message(self, app_client):
        client, mock_db, mock_rl, *_ = app_client
        mock_db.getData.return_value = make_firestore_doc(data={
            'remote_lock_id': 'guest-123',
            'timestamp':      datetime.now(timezone.utc),
        })
        mock_rl.update_pin.side_effect = PinConflictError("PIN taken")

        resp = self._sms(client, '+15085551234', '7890')

        assert resp.status_code == 200
        assert b'PIN taken' in resp.data

    def test_remotelock_error_returns_500(self, app_client):
        client, mock_db, mock_rl, *_ = app_client
        mock_db.getData.return_value = make_firestore_doc(data={
            'remote_lock_id': 'guest-123',
            'timestamp':      datetime.now(timezone.utc),
        })
        mock_rl.update_pin.side_effect = req_lib.exceptions.RequestException("Timeout")

        resp = self._sms(client, '+15085551234', '7890')

        assert resp.status_code == 500


# ---- /webhook-form -------------------------------------------------------

class TestFormWebhook:
    def test_bad_signature_rejected(self, app_client):
        client, *_ = app_client
        resp = client.post('/webhook-form',
            json={'payload': {}},
            headers={'X-Vagaro-Signature': 'wrong'})
        assert resp.status_code == 403

    def test_wrong_form_id_ignored(self, app_client):
        client, mock_db, *_ = app_client
        resp = client.post('/webhook-form',
            json={'payload': {
                'formId':               'wrong-form-id',
                'customerId':           'CUST123',
                'questionsAndAnswers':  [],
            }},
            headers={'X-Vagaro-Signature': FORUM_TOKEN})
        assert resp.status_code == 200
        mock_db.add.assert_not_called()

    def test_valid_form_stored_in_firestore(self, app_client):
        client, mock_db, *_ = app_client
        resp = client.post('/webhook-form', json={'payload': {
            'formId':     '67842fd8f276412c07c20490',
            'customerId': 'CUST123',
            'questionsAndAnswers': [
                {'question': 'First Name', 'answer': ['John']},
                {'question': 'Last Name',  'answer': ['Doe']},
                {'question': 'CELL #',     'answer': ['5085551234']},
            ],
        }}, headers={'X-Vagaro-Signature': FORUM_TOKEN})

        assert resp.status_code == 200
        mock_db.add.assert_called_once()
        stored = mock_db.add.call_args.kwargs['data']
        assert stored['first_name']   == 'John'
        assert stored['last_name']    == 'Doe'
        assert stored['phone_number'] == '5085551234'

    def test_empty_answers_skipped_gracefully(self, app_client):
        client, mock_db, *_ = app_client
        resp = client.post('/webhook-form', json={'payload': {
            'formId':     '67842fd8f276412c07c20490',
            'customerId': 'CUST123',
            'questionsAndAnswers': [
                {'question': 'Instructions', 'answer': []},  # empty, should be skipped
                {'question': 'First Name',   'answer': ['Jane']},
                {'question': 'Last Name',    'answer': ['Smith']},
                {'question': 'CELL #',       'answer': ['5085559876']},
            ],
        }}, headers={'X-Vagaro-Signature': FORUM_TOKEN})

        assert resp.status_code == 200
        stored = mock_db.add.call_args.kwargs['data']
        assert stored['first_name'] == 'Jane'


# ---- /cron-expire --------------------------------------------------------

class TestCronExpire:
    def test_bad_token_rejected(self, app_client):
        client, *_ = app_client
        resp = client.post('/cron-expire', headers={'X-Cron-Token': 'wrong'})
        assert resp.status_code == 403

    def test_no_expired_docs_returns_zero(self, app_client):
        client, mock_db, *_ = app_client
        mock_db.getExpiredAutopays.return_value = []
        resp = client.post('/cron-expire', headers={'X-Cron-Token': CLEANUP_TOKEN})
        assert resp.status_code == 200
        assert b'0' in resp.data

    def test_expired_member_notified_and_deleted(self, app_client):
        client, mock_db, *_ = app_client
        expired = MagicMock()
        expired.to_dict.return_value = {'phone': '+15085551234'}
        expired.id = 'autopay-doc-1'
        mock_db.getExpiredAutopays.return_value = [expired]

        resp = client.post('/cron-expire', headers={'X-Cron-Token': CLEANUP_TOKEN})

        assert resp.status_code == 200
        assert b'1' in resp.data
        mock_db.delete.assert_called_once_with('active_autopays', 'autopay-doc-1')


# ---- /cleanup-firestore --------------------------------------------------

class TestCleanupFirestore:
    def test_bad_token_rejected(self, app_client):
        client, *_ = app_client
        resp = client.post('/cleanup-firestore', headers={'X-Cleanup-Token': 'wrong'})
        assert resp.status_code == 403

    def test_deletes_old_docs_and_returns_count(self, app_client):
        client, mock_db, *_ = app_client
        mock_doc = MagicMock()
        mock_db.getAllOldDocs.return_value = [mock_doc, mock_doc, mock_doc]
        mock_batch = MagicMock()
        mock_db.getBatch.return_value = mock_batch

        resp = client.post('/cleanup-firestore', headers={'X-Cleanup-Token': CLEANUP_TOKEN})

        assert resp.status_code == 200
        assert b'3' in resp.data
        assert mock_batch.delete.call_count == 3
        mock_batch.commit.assert_called_once()
