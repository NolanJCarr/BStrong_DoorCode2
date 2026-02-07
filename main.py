import os
import time
import requests
import traceback
import re
from flask import Flask, request, abort, jsonify
from twilio.rest import Client
from twilio.request_validator import RequestValidator
from datetime import datetime, timedelta
import pytz
from google.cloud import secretmanager
from google.cloud import firestore
import phonenumbers

app = Flask(__name__)

# --- GCP Project Configuration ---------------------------------------------
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID")

# --- Initialize Firestore Client -------------------------------------------
db = firestore.Client(database="bstrong2")

# --- Global Constants -------------------------------------
POS_MISC_CUSTOMER_ID = "WDSh-insBmIKBj0N22Zw6w=="

# --- Secret Manager Helper ----------------------------------------------
def get_secret(secret_id, version_id="latest"):
    """Fetches a secret from Google Secret Manager."""
    if not GCP_PROJECT_ID:
        raise ValueError("GCP_PROJECT_ID environment variable not set.")
        
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{GCP_PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
    try:
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        print(f"Error accessing secret: {secret_id}. Details: {e}")
        # Removed notify_developer here to avoid infinite loops during startup
        raise e

# --- Lazy Config Class (The Fix) ---------------------------------------
class Config:
    """
    Fetches secrets only when requested, preventing timeout at startup.
    """
    _secrets = {}

    @classmethod
    def get(cls, key):
        if key in cls._secrets:
            return cls._secrets[key]
        try:
            val = get_secret(key)
            cls._secrets[key] = val
            return val
        except Exception as e:
            print(f"Failed to fetch config key {key}: {e}")
            return None

# --- Global Variables ----------------------------------------------------
remote_lock_token = None
token_expiry = None
_vagaro_cached_token = None
_vagaro_expires_at = 0

membership_durations = {
    "weekend warrior": timedelta(days=2),
    "1 week pass": timedelta(weeks=1),
    "2 week pass": timedelta(weeks=2),
    "3 week pass": timedelta(weeks=3),
    "1 month gym membership": timedelta(days=30),
    "12 month autopay (9-mo @ $99/ 3 mo-free)": timedelta(days=365),
    "best rate!!! one year (pif)": timedelta(days=365),
    "2 month gym membership": timedelta(days=60),
    "3 month gym membership": timedelta(days=90),
    "6 month gym membership": timedelta(days=180),
    "day pass (not a class) - 4am-10pm for one individual, for one calendar day.": timedelta(days=0),
    "day pass": timedelta(days=0)
}

# --- SMS Helper -------------------------------------------------
def send_sms(to_phone_number, body, first_name=None, last_name=None):
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
        print(f"SMS sent to {to_phone_number}")
        return True
    except Exception as e:
        print(f"Failed SMS to {first_name or ''} {last_name or ''}: {e}")
        return False

# --- Notification Helpers ----------------------------------
def notify_developer(message):
    dev_num = Config.get("DEVELOPER_PHONE_NUMBER")
    sid = Config.get("TWILIO_ACCOUNT_SID")
    token = Config.get("TWILIO_AUTH_TOKEN")
    from_num = Config.get("TWILIO_PHONE_NUMBER")

    if not all([dev_num, sid, token, from_num]):
        print(f"Developer notification failed: Credentials not fully loaded. Message: {message}")
        return
    client = Client(sid, token)
    try:
        client.messages.create(
            body=f"B-STRONG DEV ALERT: {message}",
            from_=from_num,
            to=dev_num
        )
        print(f"Developer notified: {dev_num}")
    except Exception as e:
        print(f"Failed to notify developer: {e}")

def notify_owners(message):
    o1 = Config.get("OWNER_PHONE_NUMBER_1")
    o2 = Config.get("OWNER_PHONE_NUMBER_2")
    owners = [num for num in [o1, o2] if num]
    
    sid = Config.get("TWILIO_ACCOUNT_SID")
    token = Config.get("TWILIO_AUTH_TOKEN")
    from_num = Config.get("TWILIO_PHONE_NUMBER")

    if not all([owners, sid, token, from_num]):
        print(f"Owner notification failed: Credentials not fully loaded. Message: {message}")
        return
    client = Client(sid, token)
    for owner_number in owners:
        try:
            client.messages.create(
                body=f"B-STRONG ALERT: {message}",
                from_=from_num,
                to=owner_number
            )
            print(f"Owner notified: {owner_number}")
        except Exception as e:
            print(f"Failed to notify owner {owner_number}: {e}")

# --- RemoteLock OAuth ---------------------------------------------------
def get_access_token():
    global remote_lock_token, token_expiry
    if remote_lock_token and token_expiry and datetime.utcnow() < token_expiry:
        return remote_lock_token

    client_id = Config.get("REMOTELOCK_CLIENT_ID")
    client_secret = Config.get("REMOTELOCK_CLIENT_SECRET")
    token_url = Config.get("TOKEN_URL")

    if not all([client_id, client_secret, token_url]):
        return None

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }
    try:
        resp = requests.post(token_url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        remote_lock_token = data["access_token"]
        token_expiry = datetime.utcnow() + timedelta(seconds=data["expires_in"] - 60)
        return remote_lock_token
    except requests.exceptions.RequestException as e:
        print(f"Error getting RemoteLock token: {e}")
        notify_developer(f"Could not refresh RemoteLock token: {e}")
        return None

# --- Vagaro OAuth ----------------------------------------------------------
def get_vagaro_token():
    global _vagaro_cached_token, _vagaro_expires_at
    now = time.time()
    if _vagaro_cached_token and now < _vagaro_expires_at - 60:
        return _vagaro_cached_token
        
    client_id = Config.get("VAGARO_CLIENT_ID")
    client_secret = Config.get("VAGARO_CLIENT_SECRET")

    if not all([client_id, client_secret]):
        return None

    url = "https://api.vagaro.com/us03/api/v2/merchants/generate-access-token"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    payload = {
        "clientId": client_id,
        "clientSecretKey": client_secret
    }
    
    try:
        r = requests.post(url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()["data"]
        _vagaro_cached_token = data["access_token"]
        _vagaro_expires_at = now + data.get("expires_in", 3600)
        return _vagaro_cached_token
    
    except requests.exceptions.RequestException as e:
        print(f"Error getting Vagaro token: {e}")
        notify_developer(f"Could not refresh Vagaro token: {e.response.text if e.response else e}")
        return None

# --- Door-code Logic --------------------------------------------------
def createDoorCode(first, last, phone, membership_type):
    access_token = get_access_token()
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
        notify_developer(f"RemoteLock API error for {first} {last}: {e.response.text if e.response else e}")
        return (False, None)

    exp_date = end_utc.astimezone(est).strftime('%Y-%m-%d')
    
    if "day pass" in membership_type.lower():
        sms_body = f"Your B-STRONG door code is {pin}#. Be sure to hit the # after the numbers. Access hours are 4am-10pm. Busiest times are 8am-11am, so if you arrive at 9, plan for it to be busy. Please don't share your code with others or let anyone else in. Questions? Text Craig at 774-255-0465 or Heather at 508-685-8888. Enjoy your workout!"
    else:
        sms_body = f"Your B-STRONG door code is {pin}#. Be sure to hit the # after the numbers. If you’d like to change your door code please respond to this text with the 4 or 5 digits to set it. Your code will expire {exp_date} at 10:00 pm. Access hours are 4am-10pm. Busiest times are 8am-11am, so if you arrive at 9, plan for it to be busy. Please don't share your code with others or let anyone else in. Questions? Text Craig at 774-255-0465 or Heather at 508-685-8888. Enjoy your workout!"
    
    sms_sent = send_sms(phone, sms_body, first, last)
    return (sms_sent, guest_id)

# --- Form Webhook Handler ----------------------------------------------
@app.route("/webhook-form", methods=['POST'])
def form_webhook():
    expected_token = Config.get("FORUM_TOKEN")
    received_token = request.headers.get("X-Vagaro-Signature")
    
    if received_token != expected_token:
        abort(403, "Invalid X-Vagaro-Signature")

    data = request.json
    if not data or "payload" not in data:
        return "No valid payload found", 400

    payload = data["payload"]
    
    if payload.get("formId") != "67842fd8f276412c07c20490":
        print(f"Ignoring form webhook for formId: {payload.get('formId')}")
        return "Not the correct form, ignoring.", 200

    customer_id = payload.get("customerId")
    if not customer_id:
        print("No customerId found in form webhook.")
        return "Missing customerId", 400

    try:
        questions = payload["questionsAndAnswers"]
        first_name = questions[0]["answer"][0]
        last_name = questions[1]["answer"][0]
        phone_number_raw = questions[2]["answer"][0]
        
        doc_ref = db.collection('pending_customers').document(customer_id)
        doc_ref.set({
            'first_name': first_name,
            'last_name': last_name,
            'phone_number': phone_number_raw,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        print(f"Stored raw form data for customer {customer_id} in Firestore.")
        return "Form data stored successfully", 200

    except Exception as e:
        print(f"Error processing form webhook for customer {customer_id}: {e}")
        notify_developer(f"Failed to process form for customer {customer_id}: {e}")
        return "Error processing form data", 500

# --- Transaction Webhook Handler ----------------------------------
@app.route("/webhook-transaction", methods=["POST"])
def transaction_webhook():
    expected_token = Config.get("TRANSACTION_TOKEN")
    sig = request.headers.get("X-Vagaro-Signature")
    
    if sig != expected_token:
        print(f"Bad signature received: {sig}")
        abort(403, "Forbidden: Invalid signature.")

    data = request.get_json(silent=True)
    if not data or "payload" not in data:
        return "Invalid payload", 400

    p = data["payload"]
    item_sold = p.get("itemSold", "").lower()
    customer_id = p.get("customerId")

    print(f"Received transaction for customerId: '[{customer_id}]'")
    if customer_id and customer_id.strip() == POS_MISC_CUSTOMER_ID:
        print("Ignoring transaction for POS Miscellaneous account.")
        return "POS Miscellaneous transaction ignored", 200

    purchase_type = p.get("purchaseType")
    is_membership = purchase_type == "Membership"
    is_class_day_pass = purchase_type == "Class" and "day pass" in item_sold
    is_package_day_pass = purchase_type == "Package" and item_sold == "day pass"
    
    if not (is_membership or is_class_day_pass or is_package_day_pass):
        print(f"Ignoring non-membership/day pass purchase: Type='{purchase_type}', Item='{item_sold}'")
        return "Not a relevant purchase type", 200

    # Use userPaymentId as the primary unique identifier.
    unique_id = p.get("userPaymentId")
    if not unique_id:
        unique_id = p.get("transactionId")
        if not unique_id:
            unique_id = str(hash(frozenset(p.items())))
            print(f"No transactionId or userPaymentId found, using generated hash: {unique_id}")

    transaction_ref = db.collection('processed_transactions').document(unique_id)
    if transaction_ref.get().exists:
        print(f"Duplicate transaction item ignored: {unique_id}")
        return "Transaction item already processed", 200
    
    transaction_ref.set({'timestamp': firestore.SERVER_TIMESTAMP})

    if not customer_id:
        notify_developer("Received transaction webhook without a customerId.")
        return "Missing customerId", 400

    first = None
    last = None
    phone = None
    phone_is_valid = False

    try:
        doc_ref = db.collection('pending_customers').document(customer_id)
        doc = doc_ref.get()
        if doc.exists:
            print(f"Found pending form data for customer {customer_id} in Firestore.")
            customer_data = doc.to_dict()
            first = customer_data.get('first_name')
            last = customer_data.get('last_name')
            phone_raw_from_firestore = customer_data.get('phone_number')
            
            try:
                parsed_phone = phonenumbers.parse(phone_raw_from_firestore, "US")
                if phonenumbers.is_valid_number(parsed_phone):
                    phone = phonenumbers.format_number(parsed_phone, phonenumbers.PhoneNumberFormat.E164)
                    phone_is_valid = True
                    print(f"Valid phone number '{phone}' found in Firestore.")
                else:
                    print(f"Invalid phone number '{phone_raw_from_firestore}' in Firestore. Will use API for phone number.")
            except Exception as e:
                print(f"Error parsing phone from Firestore: {e}. Will use API for phone number.")

            doc_ref.delete()
        else:
            print(f"No pending form data for customer {customer_id}. Using API fallback.")
    except Exception as e:
        print(f"Error accessing Firestore for customer {customer_id}: {e}. Using API fallback.")
        notify_developer(f"Firestore access error for {customer_id}: {e}")

    if not phone_is_valid:
        try:
            print(f"Executing API fallback for customer {customer_id}.")
            cust = get_vagaro_customer_details(customer_id)
            if not cust:
                raise ValueError("Customer data could not be retrieved from API.")
            
            if not first:
                first = cust.get("customerFirstName")
            if not last:
                last = cust.get("customerLastName")
            
            phone_raw = cust.get("mobilePhone")

            if not phone_raw:
                raise ValueError("No mobile phone found in Vagaro profile.")

            parsed_phone = phonenumbers.parse(phone_raw, "US")
            if not phonenumbers.is_valid_number(parsed_phone):
                raise ValueError(f"Invalid phone number from API: {phone_raw}")
            phone = phonenumbers.format_number(parsed_phone, phonenumbers.PhoneNumberFormat.E164)
            print(f"Using valid phone number '{phone}' from API.")

        except Exception as e:
            print(f"Failed to get customer details via API fallback: {e}")
            customer_name = f"{first or 'Unknown'} {last or 'Customer'}"
            notify_owners(f"Failed to send code to {customer_name}")
            return "Error fetching customer data", 500

    if not (first and last and phone):
        notify_owners(f"{first or 'Unknown'} {last or 'Customer'} didn't get a door code")
        return "Incomplete customer data", 500

    print(f"Processing purchase for {first} {last} ({item_sold})")
    success, guest_id = createDoorCode(first, last, phone, item_sold)
    
    if success:
        is_day_pass = "day pass" in item_sold
        if not is_day_pass:
            try:
                ticket_ref = db.collection('pin_change_tickets').document(phone)
                ticket_ref.set({
                    'remote_lock_id': guest_id,
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
                print(f"Created PIN change ticket for {phone}")
            except Exception as e:
                print(f"Failed to create PIN change ticket for {phone}: {e}")
                notify_developer(f"Failed to create PIN ticket for {phone}: {e}")
        return "Door code created successfully", 200
    else:
        notify_owners(f"{first} {last} didn't get a door code.")
        return "Failed to create door code", 500

# --- Vagaro Customer Fetcher -------------------------------
def get_vagaro_customer_details(cust_id):
    token = get_vagaro_token()
    if not token:
        return None

    business_id = Config.get("BUSINESS_ID")
    if not business_id:
        print("Missing Business ID")
        return None

    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "accessToken": token,
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    payload = {"businessId": business_id, "customerId": cust_id}
    
    try:
        vagaro_resp = requests.post(
            "https://api.vagaro.com/us03/api/v2/customers",
            json=payload,
            headers=headers
        )
        if vagaro_resp.status_code != 200:
            print(f"Vagaro customer API returned status {vagaro_resp.status_code} with response: {vagaro_resp.text}")
        vagaro_resp.raise_for_status()
        return vagaro_resp.json().get("data")
    
    except requests.exceptions.RequestException as e:
        notify_developer(f"Vagaro API error for customer {cust_id}: {e.response.text if e.response else e}")
        return None

# --- SMS Webhook Handler for PIN Changes ----------------------
@app.route("/webhook-sms", methods=['POST'])
def sms_webhook():
    auth_token = Config.get("TWILIO_AUTH_TOKEN")
    validator = RequestValidator(auth_token)
    
    url = f"https://{request.headers.get('X-Forwarded-Host', request.host)}{request.full_path}"
    
    post_vars = request.form
    signature = request.headers.get('X-Twilio-Signature', '')

    if not validator.validate(url, post_vars, signature):
        print(f"Twilio signature validation FAILED. URL used for validation: {url}")
        return "Forbidden: Invalid Twilio signature", 403
    
    print("Twilio signature validation PASSED.")
    from_number = request.form.get('From')
    body = request.form.get('Body', '').strip()

    ticket_ref = db.collection('pin_change_tickets').document(from_number)
    ticket = ticket_ref.get()

    if not ticket.exists:
        print(f"No PIN change ticket found for {from_number}. Ignoring.")
        return "No ticket.", 200

    ticket_data = ticket.to_dict()
    remote_lock_id = ticket_data.get('remote_lock_id')
    timestamp = ticket_data.get('timestamp')

    if datetime.now(pytz.utc) > (timestamp + timedelta(hours=48)):
        send_sms(from_number, "Sorry, the 48-hour window for changing your PIN has expired.")
        ticket_ref.delete()
        return "Ticket expired.", 200

    cleaned_pin = body.replace('#', '')
    if not re.match(r'^\d{4,5}$', cleaned_pin):
        send_sms(from_number, "Invalid reponse. Please try again with just the 4 or 5 numbers you'd like for your door code.")
        return "Invalid PIN format.", 200

    access_token = get_access_token()
    if not access_token:
        notify_developer(f"Could not get RemoteLock token for PIN change for {from_number}")
        return "Internal error.", 500

    update_url = f"https://api.remotelock.com/access_persons/{remote_lock_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.lockstate+json; version=1",
        "Content-Type": "application/json"
    }
    payload = {"attributes": {"pin": cleaned_pin}}

    try:
        response = requests.put(update_url, json=payload, headers=headers)
        if response.status_code == 200:
            send_sms(from_number, f"Door code successfully set to {cleaned_pin}#")
            ticket_ref.delete()
            return "PIN updated.", 200
        elif response.status_code == 422:
            send_sms(from_number, "Sorry, that code is already in use. Please try again.")
            return "PIN taken.", 200
        else:
            response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"Failed to update PIN for {from_number}: {e}")
        notify_developer(f"RemoteLock API error on PIN update for {from_number}: {e.response.text if e.response else e}")
        send_sms(from_number, "Sorry, an error occurred while updating your code. Please contact staff.")
        return "RemoteLock error.", 500
    
    return "OK", 200


# --- Firestore Cleanup Handler --------------------------------------
@app.route("/cleanup-firestore", methods=['POST'])
def cleanup_firestore():
    cleanup_token = Config.get("CLEANUP_TOKEN")
    received_token = request.headers.get("X-Cleanup-Token")
    
    if received_token != cleanup_token:
        abort(403, "Invalid cleanup token")

    try:
        two_days_ago = datetime.now(pytz.utc) - timedelta(days=2)
        
        docs_pending = db.collection('pending_customers').where('timestamp', '<', two_days_ago).stream()
        docs_tickets = db.collection('pin_change_tickets').where('timestamp', '<', two_days_ago).stream()
        docs_transactions = db.collection('processed_transactions').where('timestamp', '<', two_days_ago).stream()

        deleted_count = 0
        batch = db.batch()
        
        for doc in docs_pending:
            batch.delete(doc.reference)
            deleted_count += 1
        
        for doc in docs_tickets:
            batch.delete(doc.reference)
            deleted_count += 1
        
        for doc in docs_transactions:
            batch.delete(doc.reference)
            deleted_count += 1

        batch.commit()

        print(f"Firestore cleanup successful. Deleted {deleted_count} old documents.")
        return f"Deleted {deleted_count} old documents.", 200

    except Exception as e:
        print(f"Error during Firestore cleanup: {e}")
        notify_developer(f"Firestore cleanup job failed: {e}")
        return "Error during cleanup", 500

# --- Health Check ----------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

# --- Main Execution -----------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
