import pytest
import pytz
import requests as req_lib
from datetime import datetime, timezone
from freezegun import freeze_time
from unittest.mock import MagicMock, patch

from services import get_next_month_anniversary, create_door_code, extend_remotelock_code

EST = pytz.timezone('US/Eastern')


def _expiry(year, month, day):
    """Make a Firestore-style EST expiry (10:05 PM) for a given date."""
    return EST.localize(datetime(year, month, day, 22, 5))


class TestGetNextMonthAnniversary:
    def test_regular_month_same_day(self):
        rl_time, fs_time = get_next_month_anniversary(_expiry(2026, 3, 15))
        assert rl_time.month == 4 and rl_time.day == 15
        assert fs_time.month == 4 and fs_time.day == 15

    def test_december_rolls_over_to_january(self):
        rl_time, fs_time = get_next_month_anniversary(_expiry(2026, 12, 20))
        assert rl_time.year == 2027 and rl_time.month == 1 and rl_time.day == 20

    def test_jan_31_clamps_to_feb_28_non_leap_year(self):
        rl_time, _ = get_next_month_anniversary(_expiry(2026, 1, 31))
        assert rl_time.month == 2 and rl_time.day == 28

    def test_jan_31_clamps_to_feb_29_leap_year(self):
        rl_time, _ = get_next_month_anniversary(_expiry(2024, 1, 31))
        assert rl_time.month == 2 and rl_time.day == 29

    def test_oct_31_clamps_to_nov_30(self):
        rl_time, _ = get_next_month_anniversary(_expiry(2026, 10, 31))
        assert rl_time.month == 11 and rl_time.day == 30

    def test_remotelock_time_is_2200_utc(self):
        rl_time, _ = get_next_month_anniversary(_expiry(2026, 3, 15))
        assert rl_time.hour == 22 and rl_time.minute == 0

    def test_firestore_time_is_2205_est(self):
        _, fs_time = get_next_month_anniversary(_expiry(2026, 3, 15))
        assert fs_time.hour == 22 and fs_time.minute == 5

    @freeze_time("2026-04-29 14:00:00")  # 10 AM EST, before cutoff
    def test_no_expiry_uses_today(self):
        rl_time, _ = get_next_month_anniversary()
        assert rl_time.month == 5 and rl_time.day == 29

    @freeze_time("2026-04-30 03:30:00")  # 10:30 PM EST on Apr 29 (UTC-5 = 03:30 UTC next day)
    def test_no_expiry_after_10pm_uses_tomorrow(self):
        rl_time, _ = get_next_month_anniversary()
        # After 10 PM EST start is pushed to tomorrow (Apr 30), so next month is May 30
        assert rl_time.month == 5 and rl_time.day == 30


class TestCreateDoorCode:
    @freeze_time("2026-04-29 14:00:00")  # 10 AM EST
    def test_success_returns_true_and_guest_id(self):
        mock_rl = MagicMock()
        mock_rl.create_access_person.return_value = ('guest-abc', '4321')

        with patch('services.send_sms', return_value=True):
            success, guest_id = create_door_code(
                'John', 'Doe', '+15085551234', '1 week pass', mock_rl)

        assert success is True
        assert guest_id == 'guest-abc'

    @freeze_time("2026-04-29 14:00:00")
    def test_start_time_is_4am_utc(self):
        mock_rl = MagicMock()
        mock_rl.create_access_person.return_value = ('guest-1', '0000')

        with patch('services.send_sms', return_value=True):
            create_door_code('John', 'Doe', '+15085551234', '1 week pass', mock_rl)

        call_kwargs = mock_rl.create_access_person.call_args.kwargs
        assert '04:00' in call_kwargs['starts_at']

    @freeze_time("2026-04-29 14:00:00")
    def test_day_pass_ends_at_10pm_same_day(self):
        mock_rl = MagicMock()
        mock_rl.create_access_person.return_value = ('guest-2', '1111')

        with patch('services.send_sms', return_value=True):
            create_door_code('John', 'Doe', '+15085551234', 'day pass', mock_rl)

        call_kwargs = mock_rl.create_access_person.call_args.kwargs
        # ends_at should be 22:00 (10 PM) on the same date as starts_at
        assert '22:00' in call_kwargs['ends_at']
        assert call_kwargs['starts_at'][:10] == call_kwargs['ends_at'][:10]

    @freeze_time("2026-04-29 14:00:00")
    def test_remotelock_failure_returns_false_none(self):
        mock_rl = MagicMock()
        mock_rl.create_access_person.side_effect = req_lib.exceptions.RequestException("Connection timeout")

        # Patch send_Dev so a unit-test RemoteLock failure doesn't text the developer.
        with patch('services.send_Dev') as mock_dev:
            success, guest_id = create_door_code(
                'John', 'Doe', '+15085551234', '1 week pass', mock_rl)

        assert success is False
        assert guest_id is None
        mock_dev.assert_called_once()

    @freeze_time("2026-04-29 14:00:00")
    def test_unknown_membership_sends_dev_alert(self):
        mock_rl = MagicMock()
        mock_rl.create_access_person.return_value = ('guest-3', '2222')

        with patch('services.send_Dev') as mock_dev, \
             patch('services.send_sms', return_value=True):
            create_door_code('John', 'Doe', '+15085551234', 'mystery plan', mock_rl)

        mock_dev.assert_called_once()
        assert 'mystery plan' in mock_dev.call_args[0][0]

    @freeze_time("2026-04-29 14:00:00")
    def test_grant_lock_access_called_after_person_created(self):
        mock_rl = MagicMock()
        mock_rl.create_access_person.return_value = ('guest-4', '3333')

        with patch('services.send_sms', return_value=True):
            create_door_code('Jane', 'Smith', '+15085559876', '1 week pass', mock_rl)

        mock_rl.grant_lock_access.assert_called_once_with('guest-4', 'test-lock-id')

    @freeze_time("2026-04-29 14:00:00")
    def test_day_pass_sms_does_not_mention_expiry_date(self):
        mock_rl = MagicMock()
        mock_rl.create_access_person.return_value = ('guest-5', '4444')

        with patch('services.send_sms', return_value=True) as mock_sms:
            create_door_code('John', 'Doe', '+15085551234', 'day pass', mock_rl)

        sms_body = mock_sms.call_args.kwargs['body']
        assert 'expire' not in sms_body.lower()

    @freeze_time("2026-04-29 14:00:00")
    def test_membership_sms_includes_expiry_date(self):
        mock_rl = MagicMock()
        mock_rl.create_access_person.return_value = ('guest-6', '5555')

        with patch('services.send_sms', return_value=True) as mock_sms:
            create_door_code('John', 'Doe', '+15085551234', '1 week pass', mock_rl)

        sms_body = mock_sms.call_args.kwargs['body']
        assert 'expire' in sms_body.lower()


class TestExtendRemoteLockCode:
    def test_success_returns_true(self):
        mock_rl = MagicMock()
        expiry = pytz.utc.localize(datetime(2026, 5, 29, 22, 0))

        result = extend_remotelock_code('guest-123', expiry, mock_rl)

        assert result is True
        mock_rl.extend_access.assert_called_once()

    def test_correct_ends_at_passed_to_client(self):
        mock_rl = MagicMock()
        expiry = pytz.utc.localize(datetime(2026, 5, 29, 22, 0))

        extend_remotelock_code('guest-123', expiry, mock_rl)

        ends_at = mock_rl.extend_access.call_args[0][1]
        assert '2026-05-29' in ends_at
        assert '22:00' in ends_at

    def test_api_failure_returns_false(self):
        mock_rl = MagicMock()
        mock_rl.extend_access.side_effect = req_lib.exceptions.RequestException("API error")
        expiry = pytz.utc.localize(datetime(2026, 5, 29, 22, 0))

        # Patch send_Dev so a unit-test failure doesn't text the developer.
        with patch('services.send_Dev') as mock_dev:
            result = extend_remotelock_code('guest-123', expiry, mock_rl)

        assert result is False
        mock_dev.assert_called_once()
