import pytz, requests
from twilio.rest import Client
from config import get_remotelock_token
from app import Config, membership_durations, DeveloperPhoneNumber 
from datetime import datetime, timedelta


def send_sms(to_phone_number, body, to_phone_number_2 = None, first_name=None, last_name=None):
    sid = Config.get("TWILIO_ACCOUNT_SID")
    token = Config.get("TWILIO_AUTH_TOKEN")
    from_num = Config.get("TWILIO_PHONE_NUMBER")

    if not all([sid, token, from_num]):
        print("Twilio credentials are not configured. Cannot send SMS.")
        return False
    client = Client(sid, token)
    try:
        client.messages.create(
            body=body,
            from_=from_num,
            to=to_phone_number
        )
        if (to_phone_number_2) != None:
            client.messages.create(
            body=body,
            from_=from_num,
            to=to_phone_number_2
        )
        print(f"SMS sent to {to_phone_number}")
        return True
    except Exception as e:
        print(f"Failed SMS to {first_name or ''} {last_name or ''}: {e}")
        return False


def createDoorCode(first, last, phone, membership_type):
    access_token = get_remotelock_token()
    if not access_token:
        return (False, None)

    lock_id = Config.get("LOCK_ID")
    if not lock_id:
        print("Missing LOCK_ID")
        return (False, None)

    est = pytz.timezone("US/Eastern")
    current_time_est = datetime.now(est)

    if current_time_est.hour < 22:
        start_day = current_time_est.date()
    else:
        start_day = current_time_est.date() + timedelta(days=1)

    start_time_utc_naive = datetime.combine(start_day, datetime.min.time()) + timedelta(hours=4)
    start_utc = pytz.UTC.localize(start_time_utc_naive)

    if "day pass" in membership_type.lower():
        end_time_utc_naive = datetime.combine(start_day, datetime.min.time()) + timedelta(hours=22)
        end_utc = pytz.UTC.localize(end_time_utc_naive)
    else:
        duration = membership_durations.get(membership_type.lower(), timedelta(days=0))
        end_time_utc_intermediate = start_utc + duration
        end_day = end_time_utc_intermediate.date()
        end_time_utc_naive = datetime.combine(end_day, datetime.min.time()) + timedelta(hours=22)
        end_utc = pytz.UTC.localize(end_time_utc_naive)

    payload = {
        "type": "access_guest",
        "attributes": {
            "name": f"{first} {last}",
            "generate_pin": True,
            "starts_at": start_utc.isoformat(),
            "ends_at": end_utc.isoformat()
        }
    }
    hdr = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.lockstate+json; version=1",
        "Content-Type": "application/json"
    }

    guest_id = None
    try:
        cr = requests.post("https://api.remotelock.com/access_persons", json=payload, headers=hdr)
        cr.raise_for_status()
        guest = cr.json()["data"]
        pin = guest["attributes"]["pin"]
        guest_id = guest["id"]

        grant = {
            "attributes": { 
                           "accessible_id": lock_id, 
                           "accessible_type": "lock", 
                           "access_schedule_id": "d18e46f1-22b4-4880-9b0b-3d1ea60441fc"
                           }
        }
        gr = requests.post(f"https://api.remotelock.com/access_persons/{guest_id}/accesses", json=grant, headers=hdr)
        gr.raise_for_status()

    except requests.exceptions.RequestException as e:
        print(f"RemoteLock API error: {e}")
        send_sms(DeveloperPhoneNumber, f"RemoteLock API error for {first} {last}: {e.response.text if e.response else e}")
        return (False, None)

    exp_date = end_utc.astimezone(est).strftime('%Y-%m-%d')
    
    if "day pass" in membership_type.lower():
        sms_body = f"Your B-STRONG door code is {pin}#. Be sure to hit the # after the numbers. Access hours are 4am-10pm. Busiest times are 8am-11am, so if you arrive at 9, plan for it to be busy. Please don't share your code with others or let anyone else in. Questions? Text Craig at 774-255-0465 or Heather at 508-685-8888. Enjoy your workout!"
    else:
        sms_body = f"Your B-STRONG door code is {pin}#. Be sure to hit the # after the numbers. If you'd like to change your door code please respond to this text with the 4 or 5 digits to set it. Your code will expire {exp_date} at 10:00 pm. Access hours are 4am-10pm. Busiest times are 8am-11am, so if you arrive at 9, plan for it to be busy. Please don't share your code with others or let anyone else in. Questions? Text Craig at 774-255-0465 or Heather at 508-685-8888. Enjoy your workout!"
    
    sms_sent = send_sms(phone, sms_body, first, last)
    return (sms_sent, guest_id)