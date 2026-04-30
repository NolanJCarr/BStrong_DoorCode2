import phonenumbers, logging
from typing import TypedDict
from twilio.rest import Client
from .config import Config

logger = logging.getLogger(__name__)


class PhoneResult(TypedDict):
    valid: bool
    number: str | None


def send_sms(
    to_phone_number: str,
    body: str,
    to_phone_number_2: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
) -> bool:
    sid = Config.get("TWILIO_ACCOUNT_SID")
    token = Config.get("TWILIO_AUTH_TOKEN")
    from_num = Config.get("TWILIO_PHONE_NUMBER")

    if not all([sid, token, from_num]):
        logger.error("Twilio credentials are not configured. Cannot send SMS.")
        return False

    client = Client(sid, token)
    primary_sender = from_num if to_phone_number.startswith("+1") else "B-STRONG"

    try:
        client.messages.create(body=body, from_=primary_sender, to=to_phone_number)

        if to_phone_number_2:
            secondary_sender = from_num if to_phone_number_2.startswith("+1") else "B-STRONG"
            client.messages.create(body=body, from_=secondary_sender, to=to_phone_number_2)
            logger.info(f"SMS sent to OWNERS ({to_phone_number} and {to_phone_number_2})")
        else:
            logger.info(f"SMS sent to {to_phone_number} via {primary_sender}")
        return True

    except Exception as e:
        logger.error(f"Failed SMS to {first_name or ''} {last_name or ''} ({to_phone_number}): {e}")
        return False


def send_Dev(body: str) -> bool:
    dev_phone = Config.get("DEVELOPER_PHONE_NUMBER")
    if not dev_phone:
        logger.error("DEVELOPER_PHONE_NUMBER not configured — dev alert dropped.")
        return False
    return send_sms(to_phone_number=dev_phone, body=body)


def fix_phone_number(raw_phone_number: str | None) -> PhoneResult:
    if not raw_phone_number:
        return {'valid': False, 'number': None}

    clean_num = str(raw_phone_number).strip()

    try:
        parsed = phonenumbers.parse(clean_num, "US")
        if not phonenumbers.is_valid_number(parsed):
            if not clean_num.startswith('+'):
                parsed = phonenumbers.parse("+" + clean_num, None)
            else:
                parsed = phonenumbers.parse(clean_num, None)

        if phonenumbers.is_valid_number(parsed):
            return {
                'valid': True,
                'number': phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            }

    except Exception as e:
        logger.warning(f"Phone parsing error for '{raw_phone_number}': {e}")
    return {'valid': False, 'number': raw_phone_number}
