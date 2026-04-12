import os, requests, re, pytz
from flask import Flask, request, abort
from datetime import datetime, timedelta, timezone
from config import Config
from utils import send_sms, send_Dev, fix_phone_number
from services import create_door_code, extend_remotelock_code, get_next_month_anniversary
from api_clients import get_vagaro_customer_details, get_remotelock_token
from database import Database
from google.cloud import firestore
from twilio.request_validator import RequestValidator

app = Flask(__name__)

Owner1 = Config.get("OWNER_PHONE_NUMBER_1")
Owner2 = Config.get("OWNER_PHONE_NUMBER_2")
miscCustomerID = Config.get("MISC_PERSON_CUSTID")
db = Database()

# --- Daily Cron Job for Expirations ----------------------
@app.route("/cron-expire", methods=['POST'])
def cron_expire_memberships():
    expected_token = Config.get("CLEANUP_TOKEN")
    received_token = request.headers.get("X-Cron-Token")

    if received_token != expected_token:
        abort(403, "Invalid cron token")

    try:
        expired_docs = db.getExpiredAutopays()
        count = 0

        for doc in expired_docs:
            data = doc.to_dict()
            phone = data.get('phone')

            if phone:
                sms_body = f"Your B-Strong membership has expired because no payment was received for this month"
                send_sms(to_phone_number=phone, body=sms_body)

            db.delete('active_autopays', doc.id)
            count += 1

        print(f"Cron success. Processed and texted {count} expired autopay members.")
        return f"Processed {count} expirations.", 200

    except Exception as e:
        print(f"Error during expiration cron job: {e}")
        send_Dev(f"Expiration cron job failed: {e}")
        return "Error during cron execution", 500

# --- Form Webhook Handler ----------------------------------------------
@app.route("/webhook-form", methods=['POST'])
def form_webhook():
    expected_token = Config.get("FORUM_TOKEN")
    received_token = request.headers.get("X-Vagaro-Signature")
    if received_token != expected_token:
        abort(403, "Invalid X-Vagaro-Signature")

    data = request.get_json(silent=True)
    if not data or "payload" not in data:
        return "No valid payload found", 400

    payload = data["payload"]

    if payload.get("formId") != "67842fd8f276412c07c20490":
        print(f"Ignoring form webhook for formId: {payload.get('formId')}, wrong form")
        return "Not the correct form, ignoring.", 200

    customer_id = payload.get("customerId")
    if not customer_id:
        print("No customerId found in form webhook.")
        return "Missing customerId", 400

    try:
        questions = payload["questionsAndAnswers"]

        first_name = None
        last_name = None
        phone_number = None
        for q in questions:
            question_text = q.get("question", "")
            answers_list = q.get("answer", [])

            # If the answer array is empty (like the instructions block), skip it
            if not answers_list:
                continue

            try:
                raw_answer = answers_list[0]

                # Strip out HTML tags from Vagaro's payload
                clean_answer = re.sub(r'<[^>]+>', '', raw_answer).strip()

                if "First Name" in question_text:
                    first_name = clean_answer
                elif "Last Name" in question_text:
                    last_name = clean_answer
                elif "CELL #" in question_text:
                    phone_number = clean_answer
            except IndexError:
                print(f"INDEX ERROR on this specific question: {q}")

        Person = {
            'first_name': first_name,
            'last_name': last_name,
            'phone_number': phone_number,
            'timestamp': firestore.SERVER_TIMESTAMP
        }

        db.add(collection='pending_customers', key=customer_id, data=Person)
        return "Success", 200

    except Exception as e:
        print(f"Error processing form webhook for customer {customer_id}: {e}")
        send_Dev(f"Failed to process form for customer {customer_id}: {e}")
        return "Error processing form data", 500

# --- Transaction Webhook Handler ----------------------------------
@app.route("/webhook-transaction", methods=["POST"])
def transaction_webhook():
    expected_token = Config.get("TRANSACTION_TOKEN")
    sig = request.headers.get("X-Vagaro-Signature")

    if sig != expected_token:
        print(f"Bad signature received: {sig}")
        abort(403, "Forbidden: Invalid signature.")

    payload_wrapper = request.get_json(silent=True)
    if not payload_wrapper or "payload" not in payload_wrapper:
        return "Invalid payload", 400

    payload = payload_wrapper["payload"]
    item_sold = payload.get("itemSold", "").lower()
    customer_id = payload.get("customerId")

    if customer_id and customer_id.strip() == miscCustomerID:
        print("Ignoring transaction for POS Miscellaneous account.")
        return "POS Miscellaneous transaction ignored", 200

    purchase_type = payload.get("purchaseType")
    is_membership_or_autopay = purchase_type == "Membership"
    is_class_day_pass = purchase_type == "Class" and "day pass" in item_sold and "4am-10pm" in item_sold
    is_package_day_pass = purchase_type == "Package" and item_sold == "day pass"

    if not (is_membership_or_autopay or is_class_day_pass or is_package_day_pass):
        return "Not a relevant purchase type", 200

    print(f"Received VALID transaction: {item_sold} for customerId: '[{customer_id}]'")

    unique_id = payload.get("userPaymentId")
    if not unique_id:
        unique_id = payload.get("transactionId")

    if db.checkIfExists('processed_transactions', unique_id):
        print(f"Duplicate transaction detected: {unique_id}. Skipping.")
        return "Duplicate transaction", 200

    try:
        db.add('processed_transactions', unique_id, {'timestamp': firestore.SERVER_TIMESTAMP})
    except Exception as e:
        print(f"Error saving transaction to Firestore: {e}")

    first = None
    last = None
    phone = None
    phone_is_valid = False

    try:
        firestore_doc = db.getData('pending_customers', customer_id)
        if firestore_doc.exists:
            print(f"Found pending form data for customer {customer_id} in Firestore.")
            customer_data = firestore_doc.to_dict()
            first = customer_data.get('first_name')
            last = customer_data.get('last_name')
            phone_raw_from_firestore = customer_data.get('phone_number')

            try:
                phone_result = fix_phone_number(phone_raw_from_firestore)

                if phone_result.get('valid'):
                    phone_is_valid = True
                    phone = phone_result.get('number')
                    print(f"Valid phone number '{phone}' found in Firestore.")

            except Exception as e:
                print(f"Error parsing phone from Firestore: {e}. Will use API for phone number.")

            db.delete('pending_customers', customer_id)

        else:
            print(f"No pending form data for customer {customer_id}. Using API fallback.")

    except Exception as e:
        print(f"Error accessing Firestore for customer {customer_id}: {e}. Using API fallback.")
        send_Dev(f"Firestore access error for {customer_id}: {e}")

    if not phone_is_valid:
        try:
            print(f"Executing API fallback for customer {customer_id}. with name: ({first}) ({last})")
            cust = get_vagaro_customer_details(customer_id)
            if not cust:
                raise ValueError("Customer data could not be retrieved from API.")

            if not first:
                first = cust.get("customerFirstName")
            if not last:
                last = cust.get("customerLastName")

            phone_raw = cust.get("mobilePhone")

            if not phone_raw:
                print(f"No valid phone numbers from form or inside Vagaro")
                raise ValueError("No mobile phone found in Vagaro profile.")

            result = fix_phone_number(phone_raw)
            if result.get('valid'):
                phone = result.get("number")
                print(f"Using valid phone number '{phone}' from API.")
            else:
                phone = phone_raw
                print(f"Phone fixer could not verify '{phone_raw}'. Trying raw.")

        except Exception as e:
            print(f"Failed to get customer details via API fallback: {e}")
            customer_name = f"{first or 'Unknown'} {last or 'Customer'}"
            send_sms(to_phone_number=Owner1, body=f"Failed to send code to {customer_name}", to_phone_number_2=Owner2)
            return "Error fetching customer data", 500

    if not (first and last and phone):
        send_sms(to_phone_number=Owner1, body=f"{first or 'Unknown'} {last or 'Customer'} didn't get a door code", to_phone_number_2=Owner2)
        return "Incomplete customer data", 500

    print(f"Processing purchase for {first} {last}: ({item_sold})")

    if "monthly" in item_sold and "autopay" in item_sold:

        autopay_doc = db.getData('active_autopays', customer_id)

        if autopay_doc.exists:
            print(f"Existing 12-month autopay found for {first} {last}. Extending code.")
            autopay_data = autopay_doc.to_dict()
            guest_id = autopay_data.get('remote_lock_id')
            current_expiry = autopay_data.get('expireAt')

            rl_time, firestore_time = get_next_month_anniversary(current_expiry)

            extension_success = extend_remotelock_code(guest_id, rl_time)

            if extension_success:
                db.update('active_autopays', customer_id, {'expireAt': firestore_time})

                db.add('pin_change_tickets', phone, {'remote_lock_id': guest_id, 'timestamp': firestore.SERVER_TIMESTAMP})
                print(f"Created PIN change ticket for {first} {last} with number: {phone}")

                exp_date_str = firestore_time.strftime('%Y-%m-%d')

                sms_body = f"{first}, your B-Strong monthly payment was received and your door code has been extended and will now expire {exp_date_str} at 10:00 pm. If you'd like to change your PIN, reply to this message with a 4 or 5 digit number within the next 48 hours."
                send_sms(to_phone_number=phone, body=sms_body)
                return "Autopay code extended", 200
            else:
                send_sms(to_phone_number=Owner1, body=f"Failed to extend RemoteLock code for {first} {last}.", to_phone_number_2=Owner2)
                return "Failed to extend code", 500

        else:
            print(f"First month of 12-month autopay for {first} {last}. Creating new code.")

            rl_time, firestore_time = get_next_month_anniversary()

            success, guest_id = create_door_code(first, last, phone, item_sold, force_end_utc=rl_time)

            if success:
                db.add('active_autopays', customer_id, {
                    'remote_lock_id': guest_id,
                    'expireAt': firestore_time,
                    'phone': phone,
                    'first_name': first,
                    'last_name': last
                })
                db.add('pin_change_tickets', phone, {'remote_lock_id': guest_id, 'timestamp': firestore.SERVER_TIMESTAMP})
                print(f"Created PIN change ticket for {first} {last} with number: {phone}")
                return "First month autopay code created", 200
            else:
                send_sms(to_phone_number=Owner1, body=f"{first} {last} didn't get a door code for their new autopay.", to_phone_number_2=Owner2)
                return "Failed to create first month code", 500

    success, guest_id = create_door_code(first, last, phone, item_sold)

    if success:
        is_day_pass = "day pass" in item_sold
        if not is_day_pass:
            try:
                db.add('pin_change_tickets', phone, {'remote_lock_id': guest_id, 'timestamp': firestore.SERVER_TIMESTAMP})
                print(f"Created PIN change ticket for {first} {last} with number: {phone}")
            except Exception as e:
                print(f"Failed to create PIN change ticket for {phone}: {e}")
                send_Dev(f"Failed to create PIN ticket for {phone}: {e}")
        return "Door code created successfully", 200

    else:
        send_sms(to_phone_number=Owner1, body=f"{first} {last} didn't get a door code.", to_phone_number_2=Owner2)
        return "Failed to create door code", 500


# --- SMS Webhook Handler for PIN Changes ----------------------
@app.route("/webhook-sms", methods=['POST'])
def sms_pin_changes():
    auth_token = Config.get("TWILIO_AUTH_TOKEN")
    validator = RequestValidator(auth_token)

    path = request.full_path if request.query_string else request.path
    url = f"https://{request.headers.get('X-Forwarded-Host', request.host)}{path}"

    post_vars = request.form
    signature = request.headers.get('X-Twilio-Signature', '')

    if not validator.validate(url, post_vars, signature):
        print(f"Twilio signature validation FAILED. URL used for validation: {url}")
        return "Forbidden: Invalid Twilio signature", 403

    print("Twilio signature validation PASSED.")
    from_number = request.form.get('From')
    body = request.form.get('Body', '').strip()

    ticket = db.getData('pin_change_tickets', from_number)

    if not ticket.exists:
        print(f"No PIN change ticket found for {from_number}. Ignoring.")
        return "No ticket.", 200

    ticket_data = ticket.to_dict()
    remote_lock_id = ticket_data.get('remote_lock_id')
    timestamp = ticket_data.get('timestamp')

    if datetime.now(pytz.utc) > (timestamp + timedelta(hours=48)):
        send_sms(to_phone_number=from_number, body="Sorry, the 48-hour window for changing your PIN has expired.")
        db.delete('pin_change_tickets', from_number)
        return "Ticket expired.", 200

    cleaned_pin = body.replace('#', '').strip()
    if not re.match(r'^\d{4,5}$', cleaned_pin):
        send_sms(to_phone_number=from_number, body="Invalid response. Please try again with just the 4 or 5 numbers you'd like for your door code.")
        return "Invalid PIN format.", 200

    access_token = get_remotelock_token()
    if not access_token:
        send_Dev(f"Could not get RemoteLock token for PIN change for {from_number}")
        return "Internal error.", 500

    update_url = f"https://api.remotelock.com/access_persons/{remote_lock_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.lockstate+json; version=1",
        "Content-Type": "application/json"
    }
    pin_payload = {"attributes": {"pin": cleaned_pin}}

    try:
        response = requests.put(update_url, json=pin_payload, headers=headers, timeout=10)
        if response.status_code == 200:
            send_sms(to_phone_number=from_number, body=f"Door code successfully set to {cleaned_pin}#")
            db.delete('pin_change_tickets', from_number)
            return "PIN updated.", 200

        elif response.status_code == 422:
            send_sms(to_phone_number=from_number, body="Sorry, that code is already in use. Please try again.")
            return "PIN taken.", 200

        else:
            response.raise_for_status()

    except requests.exceptions.RequestException as e:
        print(f"Failed to update PIN for {from_number}: {e}")
        send_Dev(f"RemoteLock API error on PIN update for {from_number}: {e.response.text if e.response else e}")
        send_sms(to_phone_number=from_number, body="Sorry, an error occurred while updating your code. Please contact staff.")
        return "RemoteLock error.", 500

    return "OK", 200


@app.route("/cleanup-firestore", methods=['POST'])
def cleanup_firestore():
    cleanup_token = Config.get("CLEANUP_TOKEN")
    received_token = request.headers.get("X-Cleanup-Token")

    if received_token != cleanup_token:
        abort(403, "Invalid cleanup token")

    try:
        all_tickets = db.getAllOldDocs()
        deleted_count = 0
        batch = db.getBatch()

        for doc in all_tickets:
            batch.delete(doc.reference)
            deleted_count += 1

        batch.commit()

        print(f"Firestore cleanup successful. Deleted {deleted_count} old documents.")
        return f"Deleted {deleted_count} old documents.", 200

    except Exception as e:
        print(f"Error during Firestore cleanup: {e}")
        send_Dev(f"Firestore cleanup job failed: {e}")
        return "Error during cleanup", 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
