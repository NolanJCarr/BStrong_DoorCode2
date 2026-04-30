import pytest
import requests as req_lib
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from api_clients import RemoteLockClient, PinConflictError

FAKE_TOKEN = "fake-access-token"


@pytest.fixture
def rl_client():
    """RemoteLockClient with a pre-loaded token so _get_token() never hits the network."""
    client = RemoteLockClient()
    client._token = FAKE_TOKEN
    client._token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    return client


def mock_response(status_code: int = 200, json_data: dict = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status.return_value = None
    return resp


# ---- _request_with_retry core behaviour ---------------------------------

class TestRequestWithRetry:
    def test_success_first_attempt_no_retry(self, rl_client):
        with patch('api_clients.requests.request', return_value=mock_response()) as mock_req, \
             patch('api_clients.time.sleep') as mock_sleep:
            rl_client._request_with_retry('GET', 'https://example.com')
        assert mock_req.call_count == 1
        mock_sleep.assert_not_called()

    def test_retry_fires_on_read_timeout_then_succeeds(self, rl_client):
        good_resp = mock_response()
        with patch('api_clients.requests.request', side_effect=[
            req_lib.exceptions.ReadTimeout("timed out"),
            good_resp,
        ]) as mock_req, patch('api_clients.time.sleep') as mock_sleep:
            result = rl_client._request_with_retry('POST', 'https://example.com')
        assert mock_req.call_count == 2
        mock_sleep.assert_called_once_with(2)
        assert result is good_resp

    def test_retry_fires_on_connection_error_then_succeeds(self, rl_client):
        good_resp = mock_response()
        with patch('api_clients.requests.request', side_effect=[
            req_lib.exceptions.ConnectionError("refused"),
            good_resp,
        ]) as mock_req, patch('api_clients.time.sleep'):
            rl_client._request_with_retry('POST', 'https://example.com')
        assert mock_req.call_count == 2

    def test_both_attempts_fail_raises_exception(self, rl_client):
        with patch('api_clients.requests.request',
                   side_effect=req_lib.exceptions.ReadTimeout("timed out")), \
             patch('api_clients.time.sleep'):
            with pytest.raises(req_lib.exceptions.ReadTimeout):
                rl_client._request_with_retry('POST', 'https://example.com')

    def test_only_one_retry_ever_fired(self, rl_client):
        with patch('api_clients.requests.request',
                   side_effect=req_lib.exceptions.ReadTimeout("timed out")) as mock_req, \
             patch('api_clients.time.sleep'):
            with pytest.raises(req_lib.exceptions.ReadTimeout):
                rl_client._request_with_retry('POST', 'https://example.com')
        assert mock_req.call_count == 2  # original + exactly one retry


# ---- create_access_person -----------------------------------------------

class TestCreateAccessPerson:
    def test_success_returns_guest_id_and_pin(self, rl_client):
        with patch('api_clients.requests.request', return_value=mock_response(json_data={
            "data": {"id": "guest-abc", "attributes": {"pin": "4321"}}
        })), patch('api_clients.time.sleep'):
            guest_id, pin = rl_client.create_access_person(
                "John Doe", "2026-05-01T04:00:00Z", "2026-06-01T22:00:00Z")
        assert guest_id == "guest-abc"
        assert pin == "4321"

    def test_uses_15s_timeout(self, rl_client):
        with patch('api_clients.requests.request', return_value=mock_response(json_data={
            "data": {"id": "g", "attributes": {"pin": "0000"}}
        })) as mock_req:
            rl_client.create_access_person("J D", "2026-05-01T04:00:00Z", "2026-06-01T22:00:00Z")
        assert mock_req.call_args.kwargs['timeout'] == 15

    def test_retries_on_timeout_and_returns_pin(self, rl_client):
        good_resp = mock_response(json_data={
            "data": {"id": "guest-retry", "attributes": {"pin": "9999"}}
        })
        with patch('api_clients.requests.request', side_effect=[
            req_lib.exceptions.ReadTimeout("timed out"),
            good_resp,
        ]) as mock_req, patch('api_clients.time.sleep'):
            guest_id, pin = rl_client.create_access_person(
                "Jane Smith", "2026-05-01T04:00:00Z", "2026-06-01T22:00:00Z")
        assert mock_req.call_count == 2
        assert guest_id == "guest-retry"
        assert pin == "9999"


# ---- grant_lock_access --------------------------------------------------

class TestGrantLockAccess:
    def test_success_does_not_raise(self, rl_client):
        with patch('api_clients.requests.request', return_value=mock_response()), \
             patch('api_clients.time.sleep'):
            rl_client.grant_lock_access("guest-123", "lock-456")

    def test_retries_on_timeout(self, rl_client):
        with patch('api_clients.requests.request', side_effect=[
            req_lib.exceptions.ReadTimeout("timed out"),
            mock_response(),
        ]) as mock_req, patch('api_clients.time.sleep'):
            rl_client.grant_lock_access("guest-123", "lock-456")
        assert mock_req.call_count == 2


# ---- update_pin ---------------------------------------------------------

class TestUpdatePin:
    def test_success_does_not_raise(self, rl_client):
        with patch('api_clients.requests.request', return_value=mock_response()), \
             patch('api_clients.time.sleep'):
            rl_client.update_pin("guest-123", "1234")

    def test_422_raises_pin_conflict_not_retried(self, rl_client):
        with patch('api_clients.requests.request', return_value=mock_response(422)) as mock_req, \
             patch('api_clients.time.sleep') as mock_sleep:
            with pytest.raises(PinConflictError):
                rl_client.update_pin("guest-123", "1234")
        assert mock_req.call_count == 1  # 422 is an HTTP response, not a network error — no retry
        mock_sleep.assert_not_called()

    def test_retries_on_timeout(self, rl_client):
        with patch('api_clients.requests.request', side_effect=[
            req_lib.exceptions.ReadTimeout("timed out"),
            mock_response(),
        ]) as mock_req, patch('api_clients.time.sleep'):
            rl_client.update_pin("guest-123", "1234")
        assert mock_req.call_count == 2


# ---- extend_access ------------------------------------------------------

class TestExtendAccess:
    def test_success_does_not_raise(self, rl_client):
        with patch('api_clients.requests.request', return_value=mock_response()), \
             patch('api_clients.time.sleep'):
            rl_client.extend_access("guest-123", "2026-06-01T22:00:00Z")

    def test_retries_on_timeout(self, rl_client):
        with patch('api_clients.requests.request', side_effect=[
            req_lib.exceptions.ReadTimeout("timed out"),
            mock_response(),
        ]) as mock_req, patch('api_clients.time.sleep'):
            rl_client.extend_access("guest-123", "2026-06-01T22:00:00Z")
        assert mock_req.call_count == 2
