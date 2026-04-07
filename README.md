B-Strong Gym Automation
This service automates door access for B-Strong Gym by linking Vagaro (bookings), RemoteLock (hardware), and Twilio (SMS). It ensures every member receives a unique access code immediately after purchase, whether they sign up online or in person.

Core Workflow
Trigger: A membership or day pass is purchased in Vagaro.

Data Retrieval: The system pulls the member's name and phone number from a pre-filled signup form (Firestore) or falls back to the Vagaro API for in-person walk-ins.

Code Creation: A time-synced PIN is generated via RemoteLock.

Delivery: The PIN is texted to the member instantly.

Customization: Members can reply to the text within 48 hours to change their PIN to a custom 4 or 5-digit number.

Features
Intelligent Fallback: Handles data gaps automatically so no customer is left locked out.

Global Reach: Uses Alphanumeric Sender IDs to ensure reliable delivery to international members (UK, Serbia, etc.).

Error Reporting: Differentiates between technical bugs (alerts developers) and customer data issues (alerts owners).

Self-Cleaning: Automated jobs wipe stale transaction data every 48 hours to keep the database lean.

Tech Stack
Engine: Python (Flask) on GCP Cloud Run.

Database: GCP Firestore for temporary state and duplicate prevention.

Security: All API keys and environment variables are managed via GCP Secret Manager and Cloudflare Secrets.

Integrations: Vagaro Webhooks, Twilio Programmable SMS, and RemoteLock Connect API.

Endpoints
POST /webhook-form: Captures member info from Vagaro forms.

POST /webhook-transaction: The main engine that processes purchases and sends codes.

POST /webhook-sms: Manages incoming PIN change requests.

POST /cleanup-firestore: Maintenance task triggered by Cloud Scheduler.
