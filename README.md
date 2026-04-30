# BStrong Door Code Automation

Automated door access system for B-Strong Gym. When a member purchases a membership or day pass through Vagaro, they receive a unique time-synced PIN via SMS within seconds — no staff involvement required. Members can reply to the text within 48 hours to set a custom PIN.

---

## How It Works

```
Vagaro purchase → Webhook → Firestore / Vagaro API → RemoteLock PIN → Twilio SMS
```

1. **Purchase** — Member buys a membership or day pass in Vagaro
2. **Data lookup** — System pulls name and phone from Firestore (online signup) or the Vagaro API (walk-in)
3. **PIN creation** — A time-synced access code is created in RemoteLock with exact start/end times
4. **SMS delivery** — PIN is texted to the member immediately
5. **PIN change window** — Member can reply to the SMS within 48 hours to set a custom 4–5 digit PIN

---

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12, Flask, Gunicorn |
| Hosting | GCP Cloud Run |
| Database | GCP Firestore (`bstrong2`) |
| Secrets | GCP Secret Manager |
| CI/CD | Cloud Build → Artifact Registry → Cloud Run |
| Proxy | Cloudflare Worker |
| SMS | Twilio Programmable SMS |
| Booking | Vagaro API |
| Lock hardware | RemoteLock Connect API |

---

## Endpoints

| Endpoint | Trigger | Auth |
|---|---|---|
| `GET /health` | Cloud Run readiness probe | None |
| `POST /webhook-form` | Vagaro signup form submission | `X-Vagaro-Signature` |
| `POST /webhook-transaction` | Vagaro purchase — main flow | `X-Vagaro-Signature` |
| `POST /webhook-sms` | Twilio inbound SMS (PIN change) | Twilio signature |
| `POST /cron-expire` | Daily autopay expiration check | `X-Cron-Token` |
| `POST /cleanup-firestore` | 48-hour database cleanup | `X-Cleanup-Token` |

---

## Access Time Rules

- **Start time**: 4:00 AM the next day (or same day if purchased after 10 PM)
- **Day pass**: expires 10:00 PM same day — no PIN change allowed
- **Week pass**: expires 10:00 PM on the last day
- **Monthly autopay**: renews to the same day next month, expires 10:05 PM
- **Yearly pass**: expires 10:00 PM one year later
- **Weekend Warrior**: treated as a 2-day pass
- All times calculated in US/Eastern (EST/EDT)

---

## Key Features

- **Automatic fallback** — If form data isn't in Firestore, the system falls back to the Vagaro API so no member is left without a code
- **Monthly autopay detection** — Recognizes returning autopay members and extends their existing code instead of creating a duplicate
- **Retry logic** — RemoteLock calls retry once after 2 seconds on network failure (15s timeout, max 32s total) to handle transient API issues
- **International SMS** — Uses alphanumeric sender ID (`B-STRONG`) for reliable delivery to international members
- **Smart error routing** — Technical errors alert the developer; missing customer data alerts the gym owners
- **Self-cleaning database** — Stale records purged every 48 hours automatically

---

## Project Structure

```
app.py                        Flask entry point and webhook route handlers
bstrong/
  api_clients.py              RemoteLockClient and VagaroClient (token caching, retry logic)
  config.py                   Secret loading and MEMBERSHIP_DURATIONS constants
  database.py                 All Firestore operations
  services.py                 Business logic: PIN creation, time calculations, autopay
  utils.py                    SMS helpers and phone number parsing
cloudflare/
  cloudflare_worker.js        Cloudflare Worker proxying Vagaro API calls
tests/                        77 tests across routes, services, utils, and API clients
cloudbuild.yaml               CI/CD pipeline: build → push → deploy
Dockerfile                    Cloud Run container
```

---

## CI/CD

Pushing to `main` triggers an automatic deploy via Cloud Build:

```
git push origin main → Cloud Build → Artifact Registry → Cloud Run (bstrong-webhook-service)
```

All development work happens on the `dev` branch. Changes are merged into `main` via pull request when ready to deploy.

---

## Local Development

```bash
pip install -r requirements.txt
export GCP_PROJECT_ID=<your-project-id>
export GOOGLE_APPLICATION_CREDENTIALS=<path-to-service-account-json>
python app.py
```

## Running Tests

```bash
pip install -r requirements-test.txt
python3 -m pytest tests/ -v
```

---

## Deployment

Deploys automatically on merge to `main`. To deploy manually:

```bash
gcloud run deploy bstrong-webhook-service --source . --platform managed --region us-central1 --port 8080
```
