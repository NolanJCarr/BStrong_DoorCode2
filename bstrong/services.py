import pytz, requests, calendar, logging
from .config import MEMBERSHIP_DURATIONS, Config
from .utils import send_Dev, send_sms
from .api_clients import RemoteLockClient
from datetime import datetime, timedelta, time

logger = logging.getLogger(__name__)


def create_door_code(
    first: str,
    last: str,
    phone: str,
    membership_type: str,
    rl_client: RemoteLockClient,
    force_end_utc: datetime | None = None,
) -> tuple[bool, str | None]:
    lock_id = Config.get("LOCK_ID")
    if not lock_id:
        logger.error("Missing LOCK_ID in config.")
        return (False, None)

    est = pytz.timezone("US/Eastern")
    current_time_est = datetime.now(est)

    if current_time_est.hour < 22:
        start_day = current_time_est.date()
    else:
        start_day = current_time_est.date() + timedelta(days=1)

    start_time_est = est.localize(datetime.combine(start_day, time(4, 0)))
    start_utc = start_time_est.replace(tzinfo=pytz.UTC)

    if force_end_utc:
        end_utc = force_end_utc
    elif "day pass" in membership_type.lower():
        end_time_est = est.localize(datetime.combine(start_day, time(22, 0)))
        end_utc = end_time_est.replace(tzinfo=pytz.UTC)
    elif "1 month" in membership_type.lower():
        rl_time, _ = get_next_month_anniversary()
        end_utc = rl_time
    else:
        duration = MEMBERSHIP_DURATIONS.get(membership_type.lower())
        if duration is None:
            logger.warning(f"Unknown membership type '{membership_type}' for {first} {last}. Defaulting to same-day access.")
            send_Dev(f"Unknown membership type received: '{membership_type}' for {first} {last}. Defaulted to same-day access.")
            duration = timedelta(days=0)
        end_moment_est = start_time_est + duration
        end_time_est = est.localize(datetime.combine(end_moment_est.date(), time(22, 0)))
        end_utc = end_time_est.replace(tzinfo=pytz.UTC)

    logger.info(f"RemoteLock time window for {first} {last}: start={start_utc.isoformat()} end={end_utc.isoformat()} (membership='{membership_type}')")

    try:
        guest_id, pin = rl_client.create_access_person(
            name=f"{first} {last}",
            starts_at=start_utc.isoformat(),
            ends_at=end_utc.isoformat().replace("+00:00", "Z")
        )
        logger.info(f"RemoteLock access_person created for {first} {last}: guest_id={guest_id}, pin={pin}")

        rl_client.grant_lock_access(guest_id, lock_id)
        logger.info(f"RemoteLock lock access granted for guest {guest_id}")

    except (RuntimeError, requests.exceptions.RequestException) as e:
        logger.error(f"RemoteLock API error creating code for {first} {last}: {e}")
        send_Dev(f"RemoteLock API error for {first} {last}: {e}")
        return (False, None)

    exp_date = end_utc.astimezone(est).strftime('%Y-%m-%d')

    if "day pass" in membership_type.lower():
        sms_body = f"Your B-STRONG door code is {pin}#. Be sure to hit the # after the numbers. Access hours are 4am-10pm. Busiest times are 8am-11am, so if you arrive at 9, plan for it to be busy. Please don't share your code with others or let anyone else in. Questions? Text Craig at 774-255-0465 or Heather at 508-685-8888. Enjoy your workout!"
    else:
        sms_body = f"Your B-STRONG door code is {pin}#. Be sure to hit the # after the numbers. If you'd like to change your door code please respond to this text with the 4 or 5 digits to set it. Your code will expire {exp_date} at 10:00 pm. Access hours are 4am-10pm. Busiest times are 8am-11am, so if you arrive at 9, plan for it to be busy. Please don't share your code with others or let anyone else in. Questions? Text Craig at 774-255-0465 or Heather at 508-685-8888. Enjoy your workout!"

    sms_sent = send_sms(to_phone_number=phone, body=sms_body, first_name=first, last_name=last)
    return (sms_sent, guest_id)


def extend_remotelock_code(
    guest_id: str,
    new_expiration_datetime: datetime,
    rl_client: RemoteLockClient,
) -> bool:
    try:
        ends_at = new_expiration_datetime.isoformat().replace("+00:00", "Z")
        rl_client.extend_access(guest_id, ends_at)
        logger.info(f"RemoteLock code extended for guest {guest_id} to {new_expiration_datetime.isoformat()}")
        return True

    except (RuntimeError, requests.exceptions.RequestException) as e:
        logger.error(f"RemoteLock API error extending guest {guest_id}: {e}")
        send_Dev(f"RemoteLock API error extending {guest_id}: {e}")
        return False


def get_next_month_anniversary(existing_expiry: datetime | None = None) -> tuple[datetime, datetime]:
    est = pytz.timezone("US/Eastern")

    if existing_expiry:
        # Convert Firestore UTC to EST before extracting the date to avoid
        # 10:00 PM rolling over into the next day in UTC.
        est_time = existing_expiry.astimezone(est)
        start_date = est_time.date()
    else:
        current_time_est = datetime.now(est)
        if current_time_est.hour < 22:
            start_date = current_time_est.date()
        else:
            start_date = current_time_est.date() + timedelta(days=1)

    next_month = (start_date.month % 12) + 1
    next_year = start_date.year + (start_date.month // 12)

    _, max_days = calendar.monthrange(next_year, next_month)
    target_day = min(start_date.day, max_days)
    target_date = datetime(next_year, next_month, target_day).date()

    # REMOTELOCK TIME: 10:00 PM "Fake UTC" — RemoteLock reads 22:00 UTC as 10 PM display time
    remotelock_expiry = datetime.combine(target_date, time(22, 0)).replace(tzinfo=pytz.UTC)

    # FIRESTORE TIME: 10:05 PM True EST/EDT for accurate expiry comparisons
    firestore_expiry = est.localize(datetime.combine(target_date, time(22, 5)))

    return remotelock_expiry, firestore_expiry
