import os, requests, re, pytz, phonenumbers
from flask import Flask, request, abort
from datetime import datetime, timedelta
from services import send_sms
from config import Config, get_vagaro_customer_details
from google.cloud import firestore
from twilio.request_validator import RequestValidator
from Main.config import get_access_token, get_vagaro_token
from services import createDoorCode

app = Flask(__name__)

# --- Initialize Firestore Client -------------------------------------------
db1 = firestore.Client(database="bstrong2")


# --- Global Variables ----------------------------------------------------
POS_MISC_CUSTOMER_ID = "WDSh-insBmIKBj0N22Zw6w=="
DeveloperPhoneNumber = Config.get("DEVELOPER_PHONE_NUMBER")
Owner1 = Config.get("OWNER_PHONE_NUMBER_1")
Owner2 = Config.get("OWNER_PHONE_NUMBER_2")
remote_lock_token = None
token_expiry = None

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
        
        doc_ref = db1.collection('pending_customers').document(customer_id)
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
        send_sms(DeveloperPhoneNumber, f"Failed to process form for customer {customer_id}: {e}")
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

    payload = data["payload"]
    item_sold = payload.get("itemSold", "").lower()
    customer_id = payload.get("customerId")

    print(f"Received transaction: {item_sold} for customerId: '[{customer_id}]'")
    if customer_id or customer_id.strip() == POS_MISC_CUSTOMER_ID:
        print("Ignoring transaction for POS Miscellaneous account.")
        return "POS Miscellaneous transaction ignored", 200

    purchase_type = payload.get("purchaseType")
    is_membership = purchase_type == "Membership"
    is_class_day_pass = purchase_type == "Class" and "day pass" in item_sold
    is_package_day_pass = purchase_type == "Package" and item_sold == "day pass"

    if not (is_membership or is_class_day_pass or is_package_day_pass):
        print(f"Ignoring non-membership/day pass purchase: Type='{purchase_type}', Item='{item_sold}'")
        return "Not a relevant purchase type", 200

    unique_id = payload.get("userPaymentId")
    if not unique_id:
        unique_id = payload.get("transactionId")

    transaction_ref = db1.collection('processed_transactions').document(unique_id)
    if transaction_ref.get().exists:
        print(f"Duplicate transaction item ignored: {unique_id}")
        return "Transaction item already processed", 200
    
    transaction_ref.set({'timestamp': firestore.SERVER_TIMESTAMP})

    if not customer_id:
        send_sms(DeveloperPhoneNumber, "Received transaction webhook without a customerId.")
        return "Missing customerId", 400

    first = None
    last = None
    phone = None
    phone_is_valid = False

    try:
        doc_ref = db1.collection('pending_customers').document(customer_id)
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
        send_sms(DeveloperPhoneNumber, f"Firestore access error for {customer_id}: {e}")

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
            send_sms(Owner1, f"Failed to send code to {customer_name}", Owner2)
            return "Error fetching customer data", 500

    if not (first and last and phone):
        send_sms(Owner1, f"{first or 'Unknown'} {last or 'Customer'} didn't get a door code", Owner2)
        return "Incomplete customer data", 500

    print(f"Processing purchase for {first} {last} ({item_sold})")
    success, guest_id = createDoorCode(first, last, phone, item_sold)
    
    if success:
        is_day_pass = "day pass" in item_sold
        if not is_day_pass:
            try:
                ticket_ref = db1.collection('pin_change_tickets').document(phone)
                ticket_ref.set({
                    'remote_lock_id': guest_id,
                    'timestamp': firestore.SERVER_TIMESTAMP
                })
                print(f"Created PIN change ticket for {phone}")
            except Exception as e:
                print(f"Failed to create PIN change ticket for {phone}: {e}")
                send_sms(DeveloperPhoneNumber, f"Failed to create PIN ticket for {phone}: {e}")
        return "Door code created successfully", 200
    else:
        send_sms(Owner1, f"{first} {last} didn't get a door code.", Owner2)
        return "Failed to create door code", 500

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

    ticket_ref = db1.collection('pin_change_tickets').document(from_number)
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
        send_sms(DeveloperPhoneNumber, f"Could not get RemoteLock token for PIN change for {from_number}")
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
        send_sms(DeveloperPhoneNumber, f"RemoteLock API error on PIN update for {from_number}: {e.response.text if e.response else e}")
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
        
        docs_pending = db1.collection('pending_customers').where('timestamp', '<', two_days_ago).stream()
        docs_tickets = db1.collection('pin_change_tickets').where('timestamp', '<', two_days_ago).stream()
        docs_transactions = db1.collection('processed_transactions').where('timestamp', '<', two_days_ago).stream()

        deleted_count = 0
        batch = db1.batch()
        
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
        send_sms(DeveloperPhoneNumber, f"Firestore cleanup job failed: {e}")
        return "Error during cleanup", 500

# --- Health Check ----------------------------------------------------------
@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

# --- Main Execution -----------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
