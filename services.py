import pytz, requests
from api_clients import get_remotelock_token
from config import MEMBERSHIP_DURATIONS, Config, send_Dev, send_sms
from datetime import datetime, timedelta
from google.cloud import firestore


class DataBase:
    def __init__(self):
        self.database = firestore.Client(database="bstrong2")

    def checkIfExists(self, collection, key):
        reference = self.database.collection(collection).document(key)
        if reference.get().exists:
            print(f"Duplicate transaction item ignored: {key}")
            return True 
        else:
            return False


    def add(self, collection, key, data=None):
        reference = self.database.collection(collection).document(key)
        if data:
            reference.set(data)
        else:
            reference.set({})
        return f"successfully added key: {key} to the collection: {collection}. With the data: {data}", 200
    
    def getData(self, collection, key):
        reference = self.database.collection(collection).document(key)
        return reference.get()

    def delete(self, collection, key):
        reference = self.database.collection(collection).document(key)
        reference.delete()

    def getAllOldDocs(self):
        two_days_ago = datetime.now(pytz.utc) - timedelta(days=2)

        docs_pending = self.database.collection('pending_customers').where('timestamp', '<', two_days_ago).get()
        docs_tickets = self.database.collection('pin_change_tickets').where('timestamp', '<', two_days_ago).get()
        docs_transactions = self.database.collection('processed_transactions').where('timestamp', '<', two_days_ago).get()
        return docs_pending + docs_tickets + docs_transactions

    def getBatch(self):
        return self.database.batch()
    

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

    start_time_est = est.localize(datetime.combine(start_day, datetime.time(4, 0)))
    start_utc = start_time_est.replace(tzinfo=pytz.UTC)

    if "day pass" in membership_type.lower():
        end_time_est = est.localize(datetime.combine(start_day, datetime.time(22, 0)))
    else:
        duration = MEMBERSHIP_DURATIONS.get(membership_type.lower(), timedelta(days=0))
        end_moment_est = start_time_est + duration
        end_time_est = est.localize(datetime.combine(end_moment_est.date(), datetime.time(22, 0)))
    
    end_utc = end_time_est.replace(tzinfo=pytz.UTC)

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
        cr = requests.post("https://api.remotelock.com/access_persons", json=payload, headers=hdr, timeout=10)
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
        gr = requests.post(f"https://api.remotelock.com/access_persons/{guest_id}/accesses", json=grant, headers=hdr, timeout=10)
        gr.raise_for_status()

    except requests.exceptions.RequestException as e:
        print(f"RemoteLock API error: {e}")
        send_Dev(f"RemoteLock API error for {first} {last}: {e.response.text if e.response else e}")
        return (False, None)

    exp_date = end_utc.astimezone(est).strftime('%Y-%m-%d')
    
    if "day pass" in membership_type.lower():
        sms_body = f"Your B-STRONG door code is {pin}#. Be sure to hit the # after the numbers. Access hours are 4am-10pm. Busiest times are 8am-11am, so if you arrive at 9, plan for it to be busy. Please don't share your code with others or let anyone else in. Questions? Text Craig at 774-255-0465 or Heather at 508-685-8888. Enjoy your workout!"
    else:
        sms_body = f"Your B-STRONG door code is {pin}#. Be sure to hit the # after the numbers. If you'd like to change your door code please respond to this text with the 4 or 5 digits to set it. Your code will expire {exp_date} at 10:00 pm. Access hours are 4am-10pm. Busiest times are 8am-11am, so if you arrive at 9, plan for it to be busy. Please don't share your code with others or let anyone else in. Questions? Text Craig at 774-255-0465 or Heather at 508-685-8888. Enjoy your workout!"
    
    sms_sent = send_sms(to_phone_number=phone, body=sms_body, first_name=first, last_name=last)
    return (sms_sent, guest_id)
