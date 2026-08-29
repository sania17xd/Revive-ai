# AI Revenue Recovery Agent

Detects at-risk revenue (failed payments, abandoned checkouts, failed
subscriptions), diagnoses the root cause with an LLM (Groq), decides a bounded
recovery action via a policy engine with explicit stopping rules, executes
it, and logs a full audit trail. Ships with a synthetic data generator so
you can build and demo the whole loop before Razorpay is even connected.

## How the pieces map to the judging criteria

- **Problem taste** — picks one narrow, real loop (payment failure /
  cart abandonment recovery) instead of trying to cover all seven
  sub-directions in the track.
- **Build quality** — runs today, on synthetic data, with no external
  services required except your free Groq key. Structured into clean
  modules: `models`, `policy`, `diagnosis`, `executor`, `metrics`.
- **AI judgment** — the AI is used for exactly one thing it's actually
  good at (classifying a messy failure reason into a category and
  explaining why) and nowhere else. The bounds, retry caps, cooldowns, and
  stopping rules are plain code (`policy.py`) — deliberately *not* left to
  the model, because money-moving decisions need to be predictable.
- **Failure recovery** — `policy.py`'s `escalate_after` logic is the
  answer to "what broke and what did you do about it": once a case hits
  its retry cap, it stops automatically and escalates instead of retrying
  forever. Show this live in your demo (see "Demo script" below).

## Project structure

```
app/
  main.py            FastAPI app: all routes
  database.py        SQLite/SQLAlchemy setup
  models.py          Case + AuditLog tables
  policy.py          Rules engine: root cause -> action, with retry caps & cooldowns
  diagnosis.py        The one AI call: classifies root cause via Groq API
  executor.py         Executes the chosen action, writes audit log rows
  seed_data.py        Generates synthetic failed-payment/abandoned-checkout events
  metrics.py          Batch metrics: at-risk amount, recovered amount, recovery rate
  razorpay_client.py  Thin wrapper for real Razorpay test-mode payment links
```

## 1. Setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and add your **Groq API key** at minimum:
```
GROQ_API_KEY=gsk_...
```
Get one free, no card required, at https://console.groq.com
(sign up, click "API Keys" in the sidebar, "Create API Key"). Free tier:
30 requests/min, 14,400 requests/day on Llama 3.3 70B -- comfortably
enough for a hackathon batch.

Razorpay keys are optional for now — everything runs on synthetic data
without them. See step 4 below for when you're ready to add them.

## 2. Run it

```bash
uvicorn app.main:app --reload
```

Open **http://localhost:8000/dashboard** — click "Seed 100 synthetic
cases", then "Run pipeline pass" (click it 2-3 times to simulate multiple
retry rounds), then watch the metrics update.

Interactive API docs: **http://localhost:8000/docs**

## 3. How the loop works

1. `POST /seed?count=100` — generates fake failed payments / abandoned
   checkouts / failed subscriptions with realistic failure codes.
2. `POST /process` — for every pending case:
   - calls Groq to classify the root cause (`diagnosis.py`)
   - looks up the policy for that root cause (`policy.py`)
   - checks retry count against the policy's cap
   - if under the cap: executes the next action in sequence
   - if at the cap: escalates or stops (never retries forever)
   - writes an audit log row at every step
3. `GET /metrics` — aggregate ₹ at risk, ₹ recovered, recovery rate,
   breakdown by root cause.
4. `GET /cases/{id}/audit` — full explainable trail for one case, in order.

Run `/process` repeatedly (or wire it to a loop/cron) to simulate the
retry-with-cooldown cycle across a batch over "time."

## 4. Setting up Razorpay test mode (do this once you're ready for real data)

1. Go to https://dashboard.razorpay.com/signup and create an account.
2. In the dashboard, make sure you're in **Test Mode** (toggle top-left —
   it should say "Test Mode", not "Live Mode"). You never need to submit
   KYC or go live for this project.
3. Go to **Settings -> API Keys -> Generate Test Key**. Copy the
   **Key ID** and **Key Secret** into your `.env`:
   ```
   RAZORPAY_KEY_ID=rzp_test_...
   RAZORPAY_KEY_SECRET=...
   ```
4. To generate **real** failed-payment events instead of only synthetic
   ones: create a test Order via the Razorpay API or dashboard, then pay it
   using Razorpay's documented test card/UPI numbers that are designed to
   simulate specific failures (insufficient funds, bank timeout, etc.) —
   see https://razorpay.com/docs/payments/payments/test-card-upi-details/
5. To receive those events automatically instead of polling:
   - Go to **Settings -> Webhooks -> Add New Webhook**.
   - For local dev, expose your machine with `ngrok http 8000` and use
     the ngrok URL + `/webhook/razorpay` as the webhook URL.
   - Enable at least the `payment.failed` event.
   - Copy the **Webhook Secret** shown into your `.env` as
     `RAZORPAY_WEBHOOK_SECRET` (this is what verifies incoming webhooks
     are really from Razorpay, not spoofed).
6. To actually send recovery payment links instead of simulating them:
   nothing else to do — `executor.py` already calls
   `razorpay_client.create_payment_link()` automatically once
   `RAZORPAY_KEY_ID`/`SECRET` are set (checked via `is_razorpay_configured()`).

You do **not** need Razorpay set up to build, test, or demo the diagnosis,
policy, stopping-rules, and metrics parts of this project — that's the
whole point of the synthetic data generator.

## 5. Demo script (for judges)

1. Show the dashboard empty, click **Seed 100**.
2. Click **Run pipeline pass** once — point out cases moving from
   `detected` -> `diagnosed` -> action taken, and read out one case's
   `root_cause` + `diagnosis_reasoning` live.
3. Click **Run pipeline pass** 2-3 more times — watch some cases move to
   `recovered`, and watch at least one case's retry count hit its cap and
   flip to `escalated` or `stopped`. **This is your "one failure handled
   gracefully" moment** — open that case's `/cases/{id}/audit` and walk
   through the log line by line: detected -> diagnosed -> decided
   (attempt 1) -> outcome (failed) -> decided (attempt 2) -> outcome
   (failed) -> "retry cap reached, escalating" -> escalated. Nothing
   retried forever, nothing acted without a logged reason.
4. Show `/metrics` — total at risk, total recovered, recovery rate,
   breakdown by root cause. These are the honest, measured numbers the
   bar asks for.

## Known limitations (say these out loud, don't hide them)

- Retry/nudge *success* is simulated with a weighted random roll
  (`executor.py::_simulate_retry_outcome`), not a real payment webhook,
  since a hackathon can't wait days for real customers to retry. Swap this
  for real `payment.captured` webhook handling for production use.
- Notifications (reminders, nudges) are logged, not actually sent. Wiring
  real SMS/email/WhatsApp is a couple hours of work with any provider's
  API but wasn't the point of this build.
- The root-cause taxonomy is fixed and small (7 categories) on purpose —
  it's what keeps the policy engine's decisions bounded and explainable.
