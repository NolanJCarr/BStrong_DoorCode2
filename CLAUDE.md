# BStrong Door Code Automation — CLAUDE.md

## Project Overview

Automated door access system for B-Strong Gym. When a member purchases a membership or day pass through **Vagaro** (booking platform), the system automatically generates a time-synced PIN via **RemoteLock** (smart lock hardware) and delivers it via **Twilio SMS**. Members can reply to the SMS within 48 hours to customize their PIN.

## Architecture

| Layer | Technology |
|---|---|
| Runtime | Python 3.12, Flask, Gunicorn (--timeout 60) |
| Hosting | GCP Cloud Run (containerized) |
| Database | GCP Firestore (named DB: `bstrong2`) |
| Secrets | GCP Secret Manager (no `.env` files) |
| Proxy | Cloudflare Worker (`cloudflare_worker.js`) |
| SMS | Twilio |
| Booking | Vagaro API |
| Lock hardware | RemoteLock Connect API |

## File Map

```
app.py                        Flask entry point and all webhook route handlers (composition root)
bstrong/                      Core Python package
  __init__.py
  api_clients.py              RemoteLockClient and VagaroClient with token caching and retry logic
  config.py                   Secret loading (Config, get_secret) and MEMBERSHIP_DURATIONS constants
  database.py                 Database class — all Firestore operations
  services.py                 Business logic: PIN creation, time calculations, autopay
  utils.py                    send_sms, send_Dev, fix_phone_number; PhoneResult TypedDict
cloudflare/
  cloudflare_worker.js        Cloudflare Worker that proxies Vagaro API calls
tests/
  conftest.py                 Fixtures, test config, SMS routing guards
  test_api_clients.py         RemoteLockClient retry logic tests
  test_routes.py              All webhook endpoint tests
  test_services.py            Time calculation and service function tests
  test_utils.py               Phone number parsing tests
cloudbuild.yaml               Cloud Build CI/CD: build image → push to Artifact Registry → deploy to Cloud Run
Dockerfile                    Cloud Run container (Gunicorn --timeout 60)
requirements.txt              Python dependencies
requirements-test.txt         Test dependencies (pytest, freezegun)
```

`app.py` stays at the root so Gunicorn can find it via `app:app`. All business logic lives in the `bstrong/` package and uses relative imports within the package.

## Webhook Endpoints

| Endpoint | Trigger | Auth |
|---|---|---|
| `GET /health` | Cloud Run readiness probe | None |
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
- **`send_Dev` is guarded**: If `DEVELOPER_PHONE_NUMBER` is missing from Secret Manager, it logs an error and returns `False` rather than crashing

## Local Development

```bash
pip install -r requirements.txt
export GCP_PROJECT_ID=<your-project-id>
export GOOGLE_APPLICATION_CREDENTIALS=<path-to-service-account-json>
python app.py
```

## Deployment (GCP Cloud Run)

```bash
gcloud run deploy bstrong-webhook-service --source . --platform managed --region us-central1 --port 8080
```

The Cloudflare Worker (`cloudflare_worker.js`) must be deployed separately to Cloudflare and configured to proxy Vagaro API requests.

## API Calls & Reliability

**Token caching:**
- RemoteLock: cached with 30-second buffer before expiry
- Vagaro: cached with 60-second pre-expiry refresh

**RemoteLock timeouts and retry (in `RemoteLockClient`):**
- All four public methods (`create_access_person`, `grant_lock_access`, `update_pin`, `extend_access`) use a **15-second timeout**
- On any network-level failure (timeout, connection error), the call is **retried once after a 2-second wait** via `_request_with_retry()`
- HTTP error responses (e.g. 422 PIN conflict) are **not retried** — they are real responses, not transient failures
- Worst-case request time: 15s + 2s wait + 15s = 32s, safely within the 60s Gunicorn worker timeout
- Gunicorn is configured with `--timeout 60` in the Dockerfile for this reason

**Background on the timeout/retry decision**: An April 2026 incident showed RemoteLock's server processing a request successfully after our 10s client timeout fired. The member's code existed in RemoteLock but she never received her PIN SMS. Increasing to 15s + retry prevents this scenario.

**Vagaro API calls:** 10-second timeout, no retry (Vagaro is used as a fallback for customer data only).

## Code Design

**Dependency Inversion Principle is in place.** High-level modules (`services.py`, `app.py`) do not depend on low-level HTTP details directly. Instead:

- `RemoteLockClient` (in `api_clients.py`) owns all RemoteLock HTTP calls: `create_access_person`, `grant_lock_access`, `update_pin`, `extend_access`
- `VagaroClient` (in `api_clients.py`) owns token caching and `get_customer_details`
- Both are instantiated once in `app.py` and injected as parameters into service functions
- `services.py` imports `RemoteLockClient` for type annotation only — the concrete instance is always passed in by `app.py`

**`PinConflictError`** is a custom exception raised by `RemoteLockClient.update_pin()` on HTTP 422 (PIN already in use). Callers handle it explicitly with `except PinConflictError` rather than checking status codes.

**`LOCK_SCHEDULE_ID`** (the RemoteLock access schedule UUID) lives in `api_clients.py` alongside all other RemoteLock constants.

**Type hints** are present throughout all six source modules using Python 3.12 native syntax (`str | None`, `tuple[bool, str | None]`, `dict[str, Any]`, etc.). `utils.py` exports a `PhoneResult` TypedDict for the return shape of `fix_phone_number`.

## Logging

All modules use Python's `logging` module (not `print`). The root logger is configured in `app.py` with `logging.basicConfig(level=logging.INFO)`. Each module has a module-level `logger = logging.getLogger(__name__)`.

GCP Cloud Logging automatically picks up severity levels — filter by `ERROR` to see only problems, or search by transaction ID to trace a full purchase end-to-end.

Key values logged at every purchase:
- Transaction ID (`userPaymentId` or `transactionId`) — logged as soon as it's known
- RemoteLock time window (`start=... end=...`) — logged before every API call for timezone verification
- RemoteLock `guest_id` and PIN slot — logged on successful code creation
- Retry warnings — logged when `_request_with_retry` fires a second attempt
- `SMS sent to OWNERS (num1 and num2)` — logged whenever both owner numbers are texted (two-number `send_sms` calls always mean owners, never the developer)
- Member door code change — logged explicitly when a member successfully changes their PIN via the SMS service

## Testing

Run the full suite with:

```bash
pip install -r requirements-test.txt
python3 -m pytest tests/ -v
```

**77 tests** across four files:

| File | What it covers |
|---|---|
| `tests/test_api_clients.py` | `RemoteLockClient` retry logic — success with no retry, retry on ReadTimeout, retry on ConnectionError, both attempts failing, exactly one retry ever fired, 15s timeout enforced, 422 not retried |
| `tests/test_utils.py` | `fix_phone_number` — all input formats and edge cases |
| `tests/test_services.py` | Month rollover logic (Jan 31→Feb 28/29, Oct 31→Nov 30, Dec→Jan), time window calculations, `create_door_code` and `extend_remotelock_code` success/failure |
| `tests/test_routes.py` | All 6 endpoints (`/health` included) — auth, happy paths, error paths, day pass vs membership, autopay vs first month |

**SMS behaviour during tests:**
- Owner numbers (`OWNER_PHONE_NUMBER_1/2`) — silently dropped, never sent
- Developer (`+17745218808`) — real Twilio call; you will receive texts if a route hits a `send_Dev` path that is not mocked
- Member phone numbers used in tests — mocked success, no real Twilio call

Unit tests in `test_services.py` patch `send_Dev` explicitly so isolated failures do not text the developer. Route-level tests allow `send_Dev` to pass through so real error scenarios reach you.

## Future Changes

Backlog of improvements identified during code review. None are urgent — the system is running in production. Listed roughly by priority.

### Reliability / correctness
- **Atomic deduplication for `processed_transactions`** (`app.py:162-167`): The current `checkIfExists` then `add` is two round trips. If Vagaro retries a webhook quickly, both calls can pass the duplicate check and create two RemoteLock codes. Replace with a Firestore transaction or `create()` (which fails if the doc already exists) instead of `set()`.
- **Explicit final return in `_request_with_retry`** (`api_clients.py:65-74`): Control flow is correct today, but type checkers will flag the implicit `None` return path. Add an explicit `raise RuntimeError("unreachable")` after the loop or restructure.
- **Fail-fast on missing owner / misc IDs at startup** (`app.py:18-20`): `Owner1`, `Owner2`, and `miscCustomerID` are loaded via `Config.get()` at import time. If Secret Manager is flaky during cold start, those become `None` silently and owner-alert SMS later sends to `None` and fails quietly. Validate at startup and crash if missing.

### Security
- **Add a shared-secret header to the Cloudflare Worker**: `bstrong-vagaro-proxy.nolantatum6.workers.dev` is publicly callable and proxies real Vagaro credentials. Add an `X-Proxy-Auth` header (or similar) that the Worker validates before forwarding.
- **Pin or hardcode the Twilio webhook host** (`app.py:319`): `webhook-sms` builds the validation URL from `X-Forwarded-Host`. If anything in front of Cloud Run forwards an attacker-controlled value, signature validation can be bypassed. Hardcode the public host or pull from an env var.

### Build / deploy
- **Pin dependency versions** (`requirements.txt`): No version pins today. A bad upstream Twilio/Flask release could land in production. Pin to known-good versions or use a lock file. Also verify whether `PyJWT` is actually used and remove if dead.
- **Remove debug steps from Dockerfile**: `RUN ls -l` and `RUN cat requirements.txt` (lines 11–12) are leftover debugging output. Drop them.
- **Bump Gunicorn to `--workers 2`**: A single worker means a 32-second RemoteLock retry blocks every other incoming webhook. With 1 vCPU on Cloud Run, 2 workers × 4 threads is a safer default.

### Code quality
- **Refactor `webhook-transaction`**: ~170 lines of nested logic, hard to read and hard to test. Extract `_resolve_customer(customer_id)`, `_handle_autopay_extension(...)`, and `_handle_first_month_or_one_off(...)` so the route becomes a short orchestrator.
- **Use `date(...)` instead of `datetime(...).date()`** (`services.py:111`): Minor cleanup — avoids constructing a naive datetime in a timezone-sensitive function.

### Observability
- **Structured logging**: Switch to `python-json-logger` so Cloud Logging exposes `transaction_id`, `customer_id`, `guest_id` as queryable fields rather than freeform strings.
- **Request-ID middleware**: Generate a UUID per request and log it on every line. Tracing a single member's flow through the logs becomes trivial.
- **Add a real `/ready` endpoint**: Current `/health` returns 200 even if Firestore/Twilio/RemoteLock are unreachable. Cloud Run readiness probes are fine as-is, but a separate `/ready` that pings the RemoteLock token endpoint would let an uptime check catch dependency outages.

### Infrastructure
- **Replace cleanup cron with Firestore TTL policies**: Firestore now supports automatic TTL on a timestamp field. This removes the `/cleanup-firestore` route and the cron that calls it.
- **End-to-end integration test for `/webhook-transaction`**: Walk form → transaction → SMS with all clients mocked at the HTTP boundary (e.g. `responses` library). Current unit tests are thorough but skip the orchestration layer.
