B-Strong Gym Automation Service
Overview
This project is a serverless webhook handler built on Google Cloud Platform (GCP) designed to automate door code generation and management for the B-Strong Gym. It integrates with Vagaro for member purchases, Twilio for SMS communication, and RemoteLock for physical door access.

The system is designed to be robust and resilient, with a primary workflow for online signups and an automatic fallback for in-person purchases or data inconsistencies. It also includes features for members to customize their door codes and for automatic database maintenance.

Key Features
Automated Door Code Generation: Instantly creates a time-based door code via the RemoteLock API when a new membership or day pass is purchased in Vagaro.

Dual-Source Information Gathering:

Primary Path (Online): Captures member details from a Vagaro form submission and temporarily stores them in Firestore for fast processing.

Fallback Path (In-Person): If form data is not found, it automatically calls the Vagaro API to retrieve member details from their profile, ensuring both online and in-person purchases are handled.

SMS Notifications: Uses Twilio to send the new door code directly to the member's phone.

Member PIN Customization: Allows members to change their assigned door code by replying to the initial text within a 48-hour window. The system validates the new PIN and confirms the change via SMS.

Intelligent Error Handling:

Validates phone numbers from both form and API sources to ensure deliverability.

Sends specific, actionable alerts to developers (for technical issues) and business owners (for customer data issues).

Prevents duplicate processing of multi-item transactions.

Automated Database Cleanup: A scheduled job runs every 48 hours to clear out stale data from Firestore, ensuring the database remains clean and efficient.

Architecture
The application is built on a modern, serverless stack, ensuring high availability and cost-efficiency (pay-per-use).

Compute: Python Flask application running on GCP Cloud Run.

Database: GCP Firestore is used for temporary storage of form data, PIN change tickets, and processed transaction IDs.

Secrets Management: All API keys and sensitive credentials are securely stored and managed in GCP Secret Manager.

Scheduling: GCP Cloud Scheduler is used to trigger the automated database cleanup job.

External APIs:

Vagaro: For receiving form and transaction webhooks.

Twilio: For sending and receiving SMS messages.

RemoteLock: For creating and managing physical door codes.

API Endpoints
The service exposes several secure webhook endpoints:

POST /webhook-form: Receives new member information from a Vagaro form submission and stores it in Firestore.

POST /webhook-transaction: Receives purchase notifications from Vagaro. This is the core endpoint that triggers the door code generation logic.

POST /webhook-sms: Receives incoming text messages from members via Twilio to handle PIN change requests.

POST /cleanup-firestore: A secure endpoint triggered by Cloud Scheduler to perform routine database maintenance.

GET /health: A simple health check endpoint to confirm the service is running.

Setup & Configuration
For the service to run correctly, the following secrets must be configured in GCP Secret Manager:

VAGARO_CLIENT_ID

VAGARO_CLIENT_SECRET

BUSINESS_ID

REMOTELOCK_CLIENT_ID

REMOTELOCK_CLIENT_SECRET

TRANSACTION_TOKEN (Vagaro transaction webhook signature)

FORUM_TOKEN (Vagaro form webhook signature)

CLEANUP_TOKEN (A secure, self-generated token for the cleanup job)

LOCK_ID (The specific ID for the door lock in RemoteLock)

TOKEN_URL (The RemoteLock OAuth token URL)

TWILIO_ACCOUNT_SID

TWILIO_AUTH_TOKEN

TWILIO_PHONE_NUMBER

DEVELOPER_PHONE_NUMBER

OWNER_PHONE_NUMBER_1

OWNER_PHONE_NUMBER_2
