# BStrong Door Code Automation — CLAUDE.md

## Project Overview

Automated door access system for B-Strong Gym. When a member purchases a membership or day pass through **Vagaro** (booking platform), the system automatically generates a time-synced PIN via **RemoteLock** (smart lock hardware) and delivers it via **Twilio SMS**. Members can reply to the SMS within 48 hours to customize their PIN.

## Architecture

| Layer | Technology |
|---|---|
| Runtime | Python 3.12, Flask, Gunicorn |
| Hosting | GCP Cloud Run (containerized) |
| Database | GCP Firestore (named DB: `bstrong2`) |
| Secrets | GCP Secret Manager (no `.env` files) |
| Proxy | Cloudflare Worker (`cloudflare_worker.js`) |
| SMS | Twilio |
| Booking | Vagaro API |
| Lock hardware | RemoteLock Connect API |

## File Map

- `app.py` — Flask app and all webhook route handlers; instantiates clients at startup (composition root)
- `services.py` — Core business logic (PIN creation, time calculations, autopay); receives API clients as parameters
- `database.py` — `Database` class (all Firestore operations)
- `api_clients.py` — `RemoteLockClient` and `VagaroClient` classes with token caching; `PinConflictError` exception; `LOCK_SCHEDULE_ID` constant
- `config.py` — Secret loading (`Config`, `get_secret`) and business constants (`MEMBERSHIP_DURATIONS`)
- `utils.py` — Shared utilities: `send_sms`, `send_Dev`, `fix_phone_number`
- `cloudflare_worker.js` — Cloudflare Worker that proxies Vagaro API calls
- `Dockerfile` — Cloud Run container definition
- `requirements.txt` — Python dependencies
- `requirements-test.txt` — Test dependencies (pytest, freezegun); extends requirements.txt

## Webhook Endpoints

All endpoints require header-based authentication.

| Endpoint | Trigger | Auth Header |
|---|---|---|
| `POST /webhook-form` | Vagaro signup form submission | `X-Vagaro-Signature` |
| `POST /webhook-transaction` | Vagaro purchase (main flow) | `X-Vagaro-Signature` |
| `POST /webhook-sms` | Twilio inbound SMS (PIN change) | Twilio signature validation |
| `POST /cron-expire` | Daily autopay expiration check | `X-Cron-Token` |
| `POST /cleanup-firestore` | 48-hour database cleanup | `X-Cleanup-Token` |

## Main Flows

### Purchase Flow (`/webhook-transaction`)
1. Validate webhook signature and deduplicate via `processed_transactions` Firestore collection
2. Retrieve member data: Firestore `pending_customers` first, fallback to Vagaro API
3. Validate phone number via `phonenumbers` library
4. Create RemoteLock code with time-synced start/end timestamps
5. Send PIN via SMS (`B-STRONG` alphanumeric sender for international numbers)
6. Create `pin_change_tickets` record (not for day passes)

### PIN Change Flow (`/webhook-sms`)
1. Validate Twilio request signature
2. Look up `pin_change_tickets` record (48-hour window)
3. Validate PIN format (4–5 digits)
4. Update code in RemoteLock
5. Delete ticket after successful change

### Monthly Autopay Extension
- Detected when an `active_autopays` record already exists for the customer
- Extends RemoteLock code to next anniversary date
- Handles month-end edge cases (e.g., Jan 31 → Feb 28/29)

### Cron Jobs
- `cron-expire` (daily): Finds expired `active_autopays` records, notifies members via SMS, deletes records
- `cleanup-firestore` (every 48h): Purges stale documents from `pending_customers`, `pin_change_tickets`, and `processed_transactions`

## Time Calculation Rules

- **Access start**: 4:00 AM next day (if purchase is before 10 PM); otherwise 4:00 AM same day
- **Day pass end**: 10:00 PM same day
- **Week pass end**: 10:00 PM on last day of duration
- **Yearly pass end**: 10:00 PM one year later
- **Monthly autopay end**: 10:05 PM on same day next month
- **"Weekend Warrior"**: Treated as a 2-day pass (specific Vagaro product name)
- **Timezone**: All calculations run in US/Eastern (EST/EDT), `pytz` handles DST

**RemoteLock timezone quirk**: RemoteLock displays times as UTC but interprets them as local. So `22:00 UTC` is stored to represent `10:00 PM` display time. Firestore stores true EST for accurate comparisons.

## Firestore Collections (DB: `bstrong2`)

| Collection | Purpose | TTL |
|---|---|---|
| `pending_customers` | Form submission data awaiting transaction | 2 days |
| `processed_transactions` | Duplicate transaction prevention | Cleaned up every 48h |
| `pin_change_tickets` | Active PIN change windows | 48 hours |
| `active_autopays` | Monthly subscription tracking | Until expired |

## Secrets (all in GCP Secret Manager)

**Vagaro:** `VAGARO_CLIENT_ID`, `VAGARO_CLIENT_SECRET`, `FORUM_TOKEN`, `TRANSACTION_TOKEN`, `BUSINESS_ID`

**RemoteLock:** `REMOTELOCK_CLIENT_ID`, `REMOTELOCK_CLIENT_SECRET`, `LOCK_ID`

**Twilio:** `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`

**Contacts:** `OWNER_PHONE_NUMBER_1`, `OWNER_PHONE_NUMBER_2`, `DEVELOPER_PHONE_NUMBER`, `MISC_PERSON_CUSTID`

**Cron:** `CLEANUP_TOKEN`

**GCP:** `GCP_PROJECT_ID` (also an environment variable on Cloud Run)

## Key Business Rules

- **Day passes**: No PIN change allowed; access only valid until 10 PM
- **POS miscellaneous transactions** (`MISC_PERSON_CUSTID`): Explicitly ignored — these are bulk/system transactions
- **Only one Vagaro form ID** is processed; others are ignored
- **Error vs. data gap**: Technical errors alert the developer; missing customer data alerts the gym owners via SMS
- **Pre-flight validation**: All customer data must be complete before any RemoteLock call — no orphaned codes

## Local Development

```bash
pip install -r requirements.txt
export GCP_PROJECT_ID=<your-project-id>
export GOOGLE_APPLICATION_CREDENTIALS=<path-to-service-account-json>
python app.py
```

## Deployment (GCP Cloud Run)

```bash
gcloud run deploy bstrong-door-code --source . --platform managed --region us-central1 --port 8080
```

The Cloudflare Worker (`cloudflare_worker.js`) must be deployed separately to Cloudflare and configured to proxy Vagaro API requests.

## API Token Caching

- **RemoteLock**: Token cached with 30-second buffer before expiry
- **Vagaro**: Token cached with 60-second pre-expiry refresh
- All external API calls use a 10-second timeout

## Code Design

**Dependency Inversion Principle is in place.** High-level modules (`services.py`, `app.py`) do not import low-level API details directly. Instead:

- `RemoteLockClient` (in `api_clients.py`) owns all RemoteLock HTTP calls: `create_access_person`, `grant_lock_access`, `update_pin`, `extend_access`
- `VagaroClient` (in `api_clients.py`) owns token caching and `get_customer_details`
- Both are instantiated once in `app.py` and injected as parameters into service functions
- `services.py` functions accept `rl_client` as a parameter — they never import from `api_clients` directly

**`PinConflictError`** is a custom exception raised by `RemoteLockClient.update_pin()` on HTTP 422 (PIN already in use). Callers handle it explicitly with `except PinConflictError` rather than checking status codes.

**`LOCK_SCHEDULE_ID`** (the RemoteLock access schedule UUID) lives in `api_clients.py` alongside all other RemoteLock constants.

## Logging

All modules use Python's `logging` module (not `print`). The root logger is configured in `app.py` with `logging.basicConfig(level=logging.INFO)`. Each module has a module-level `logger = logging.getLogger(__name__)`.

GCP Cloud Logging automatically picks up severity levels — filter by `ERROR` to see only problems, or search by transaction ID to trace a full purchase end-to-end.

Key values logged at every purchase:
- Transaction ID (`userPaymentId` or `transactionId`) — logged as soon as it's known
- RemoteLock time window (`start=... end=...`) — logged before every API call for timezone verification
- RemoteLock `guest_id` and PIN slot — logged on successful code creation

## Testing

Run the full suite with:

```bash
pip install -r requirements-test.txt
python3 -m pytest tests/ -v
```

**59 tests** across four files:

| File | What it covers |
|---|---|
| `tests/test_utils.py` | `fix_phone_number` — all input formats and edge cases |
| `tests/test_services.py` | Month rollover logic (Jan 31→Feb 28/29, Oct 31→Nov 30, Dec→Jan), time window calculations, `create_door_code` and `extend_remotelock_code` success/failure |
| `tests/test_routes.py` | All 5 webhook endpoints — auth, happy paths, error paths, day pass vs membership, autopay vs first month |

**SMS behaviour during tests:**
- Owner numbers (`OWNER_PHONE_NUMBER_1/2`) — silently dropped, never sent
- Developer (`+17745218808`) — real Twilio call; you will receive texts if a route hits a `send_Dev` path that is not mocked
- Member phone numbers used in tests — mocked success, no real Twilio call

Unit tests in `test_services.py` patch `send_Dev` explicitly so isolated failures do not text the developer. Route-level tests allow `send_Dev` to pass through so real error scenarios reach you.
