# Revive AI - Revenue Recovery Agent

Revive AI is a FastAPI-based revenue recovery prototype for Razorpay payment
flows. It detects recoverable revenue events such as failed payments,
abandoned checkouts, and failed subscription charges, diagnoses the likely
root cause, chooses a bounded recovery action, executes that action, and keeps
a full audit trail for every case.

The project works end-to-end with synthetic data, so you can demo the recovery
loop without connecting Razorpay. Groq and Razorpay credentials are optional.

## What It Does

- Generates realistic synthetic failed-payment and checkout-abandonment cases.
- Receives Razorpay test-mode webhooks for `payment.failed` and
  `subscription.charged.failed` events.
- Uses Groq for root-cause diagnosis when `GROQ_API_KEY` is configured.
- Falls back to deterministic diagnosis from failure codes when Groq is not
  configured.
- Applies policy-based recovery rules with retry caps, cooldown checks, and
  escalation paths.
- Simulates recovery outcomes for local demos.
- Exposes metrics, case details, and audit logs through API endpoints and a
  lightweight dashboard.

## Tech Stack

- Python
- FastAPI
- SQLAlchemy
- SQLite
- Jinja2
- Groq API, optional
- Razorpay SDK, optional

## Project Structure

```text
app/
  main.py            FastAPI app, routes, Razorpay webhook, dashboard HTML
  database.py        SQLite and SQLAlchemy setup
  models.py          Case and AuditLog database models
  seed_data.py       Synthetic event generator
  diagnosis.py       Groq diagnosis plus deterministic fallback diagnosis
  policy.py          Recovery policy engine with retry caps and cooldowns
  executor.py        Action execution, simulated outcomes, audit logging
  metrics.py         Batch-level recovery metrics
  razorpay_client.py Razorpay payment-link helper for test mode

.env.example         Example environment configuration
requirements.txt     Python dependencies
revenue_recovery.db  Local SQLite database, generated/used by the app
```

## Setup

Create and activate a virtual environment:

```bash
python -m venv venv
```

On Windows PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
```

On macOS/Linux:

```bash
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create your local environment file:

```bash
cp .env.example .env
```

On Windows PowerShell, use:

```powershell
Copy-Item .env.example .env
```

## Environment Variables

The app can run without external credentials. Add these values only when you
want the optional integrations.

```env
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

RAZORPAY_KEY_ID=rzp_test_xxxxxxxxxxxxxx
RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxxxxxxx
RAZORPAY_WEBHOOK_SECRET=xxxxxxxxxxxxxxxxxxxxx

DATABASE_URL=sqlite:///./revenue_recovery.db
```

- `GROQ_API_KEY`: enables LLM-based diagnosis in `app/diagnosis.py`.
- `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`: enable Razorpay test-mode
  payment-link creation.
- `RAZORPAY_WEBHOOK_SECRET`: verifies incoming Razorpay webhook signatures.
- `DATABASE_URL`: defaults to the local SQLite database.

## Run The App

```bash
uvicorn app.main:app --reload
```

Open the dashboard:

```text
http://localhost:8000/dashboard
```

Open the interactive API docs:

```text
http://localhost:8000/docs
```

## Demo Flow

1. Start the server with `uvicorn app.main:app --reload`.
2. Open `http://localhost:8000/dashboard`.
3. Click `Seed 100 synthetic cases`.
4. Click `Run pipeline pass`.
5. Run the pipeline multiple times to simulate retry rounds.
6. Watch total at-risk amount, recovered amount, recovery rate, pending cases,
   and escalations update.
7. Open any case audit link to inspect the full decision trail.

## API Endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/seed?count=100` | Generate synthetic recovery cases |
| `POST` | `/process?limit=20` | Diagnose, decide, and act on pending cases |
| `GET` | `/cases?limit=100` | List recent cases |
| `GET` | `/cases?status=detected` | List cases filtered by status |
| `GET` | `/cases/{case_id}` | Get one case |
| `GET` | `/cases/{case_id}/audit` | Get the audit trail for one case |
| `GET` | `/metrics` | Get aggregate recovery metrics |
| `POST` | `/webhook/razorpay` | Receive Razorpay test-mode failure events |
| `GET` | `/dashboard` | Open the built-in dashboard |

## Pipeline Behavior

Each `/process` pass works through pending cases in this order:

1. Diagnose the root cause with Groq or deterministic fallback logic.
2. Store the root cause, confidence, and reasoning on the case.
3. Look up the policy rule for that root cause.
4. Check retry caps and cooldown rules.
5. Execute the chosen action.
6. Mark the case as recovered, action taken, escalated, or stopped.
7. Write audit log entries for explainability.

The default process limit is `20` cases per pass. The app sleeps briefly
between diagnosis calls to stay friendly to free-tier LLM rate limits.

## Razorpay Test Mode

Razorpay is optional for the local demo. To connect test-mode events:

1. Create a Razorpay account.
2. Switch the dashboard to Test Mode.
3. Generate test API keys from Razorpay settings.
4. Add `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET` to `.env`.
5. Expose the local server with ngrok:

   ```bash
   ngrok http 8000
   ```

6. Add a Razorpay webhook URL pointing to:

   ```text
   https://your-ngrok-url/webhook/razorpay
   ```

7. Enable at least the `payment.failed` event.
8. Add the Razorpay webhook secret to `RAZORPAY_WEBHOOK_SECRET`.

## Known Limitations

- Recovery success is simulated in `executor.py`; production usage should
  listen for real successful payment webhooks.
- Reminder and nudge actions are logged but not sent through SMS, email, or
  WhatsApp providers.
- The root-cause taxonomy is intentionally small so policy decisions stay
  explainable.
- The dashboard is embedded directly in `app/main.py` for a simple hackathon
  demo; it is not a separate frontend app.

## Notes

This project is designed around a narrow, auditable loop: detect revenue at
risk, diagnose why it failed, choose a bounded action, and stop or escalate
instead of retrying forever.
