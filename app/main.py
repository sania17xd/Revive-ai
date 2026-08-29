"""
Run with:  uvicorn app.main:app --reload
Then open: J   (interactive API playground)
           http://localhost:8000/dashboard

Suggested first run (no Razorpay needed):
  1. POST /seed?count=100          -> generates fake events
  2. POST /process                 -> runs detect->diagnose->decide->act on all pending cases
  3. GET  /metrics                 -> see recovery numbers
  4. GET  /cases/{id}/audit        -> see the full explainable trail for one case
"""
import hmac
import hashlib
import os
import time
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import Base, engine, get_db
from app.models import Case, AuditLog
from app.seed_data import generate_synthetic_cases
from app.diagnosis import diagnose_case
from app.policy import decide_action
from app.executor import execute_action, _log
from app.metrics import compute_metrics

Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI Revenue Recovery Agent")

RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")


# ---------------------------------------------------------------------------
# 1. INGESTION -- synthetic data OR real Razorpay webhooks
# ---------------------------------------------------------------------------

@app.post("/seed")
def seed(count: int = 100, db: Session = Depends(get_db)):
    """Generate `count` synthetic failed-payment / abandoned-checkout events."""
    cases = generate_synthetic_cases(db, count)
    return {"created": len(cases)}


@app.post("/webhook/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Real Razorpay test-mode webhook receiver. Point your Razorpay test-mode
    webhook URL here (use ngrok for local dev) once you have keys set up.
    Docs: https://razorpay.com/docs/webhooks/
    """
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if RAZORPAY_WEBHOOK_SECRET:
        expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

    payload = await request.json()
    event = payload.get("event", "")
    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

    if event not in ("payment.failed", "subscription.charged.failed"):
        return {"status": "ignored", "event": event}

    case = Case(
        source="razorpay_test",
        event_type="payment.failed" if event == "payment.failed" else "subscription.failed",
        order_id=entity.get("order_id", "unknown"),
        payment_id=entity.get("id"),
        customer_id=entity.get("email", entity.get("contact", "unknown")),
        amount=(entity.get("amount", 0) or 0) / 100,
        currency=entity.get("currency", "INR"),
        payment_method=entity.get("method"),
        failure_code=entity.get("error_code"),
        status="detected",
    )
    db.add(case)
    db.flush()
    db.add(AuditLog(case_id=case.id, step="detected", detail=f"Real Razorpay webhook received: {event}"))
    db.commit()
    return {"status": "recorded", "case_id": case.id}


# ---------------------------------------------------------------------------
# 2. PIPELINE -- diagnose -> decide -> act, for all pending cases
# ---------------------------------------------------------------------------

@app.post("/process")
def process_pending_cases(limit: int = 20, db: Session = Depends(get_db)):
    """
    Runs the full loop on every case that isn't already resolved:
      detected/action_taken -> diagnose (Groq) -> decide (policy) -> execute -> log
    Call this repeatedly (e.g. a button in your dashboard, or a cron loop)
    to simulate the retry/cooldown cycle over a batch.

    NOTE: `limit` defaults to 20 (not 200) and each diagnosis call after
    the first is spaced out with a short delay -- Groq's free tier caps
    tokens-per-minute, and blasting through 100 cases in a few seconds
    will hit that ceiling. Click "Run pipeline pass" multiple times to
    work through a big batch in manageable chunks, or raise the limit
    once you're on a paid tier.
    """
    pending = (
        db.query(Case)
        .filter(Case.status.in_(["detected", "action_taken"]))
        .limit(limit)
        .all()
    )

    processed = 0
    for i, case in enumerate(pending):
        if i > 0:
            time.sleep(1.5)  # stay comfortably under Groq's free-tier rate limit

        diagnosis = diagnose_case(case)
        case.root_cause = diagnosis["root_cause"]
        case.diagnosis_confidence = diagnosis["confidence"]
        case.diagnosis_reasoning = diagnosis["reasoning"]
        case.status = "diagnosed"
        _log(db, case, "diagnosed",
             f"root_cause={diagnosis['root_cause']} confidence={diagnosis['confidence']:.2f} "
             f"reasoning: {diagnosis['reasoning']}")
        db.commit()

        decision = decide_action(case)
        execute_action(db, case, decision["action"], decision["reason"])
        processed += 1

    return {"processed": processed, "remaining_pending": len(pending) - processed if processed < limit else "unknown"}


# ---------------------------------------------------------------------------
# 3. READ ENDPOINTS -- cases, audit trail, metrics
# ---------------------------------------------------------------------------

@app.get("/cases")
def list_cases(status: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = db.query(Case)
    if status:
        q = q.filter(Case.status == status)
    cases = q.order_by(desc(Case.created_at)).limit(limit).all()
    return [
        {
            "id": c.id, "event_type": c.event_type, "status": c.status,
            "amount": c.amount, "root_cause": c.root_cause,
            "retry_count": c.retry_count, "recovered_amount": c.recovered_amount,
        }
        for c in cases
    ]


@app.get("/cases/{case_id}")
def get_case(case_id: int, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return {
        "id": case.id, "source": case.source, "event_type": case.event_type,
        "order_id": case.order_id, "amount": case.amount, "currency": case.currency,
        "payment_method": case.payment_method, "failure_code": case.failure_code,
        "status": case.status, "root_cause": case.root_cause,
        "diagnosis_confidence": case.diagnosis_confidence,
        "diagnosis_reasoning": case.diagnosis_reasoning,
        "retry_count": case.retry_count, "recovered_amount": case.recovered_amount,
    }


@app.get("/cases/{case_id}/audit")
def get_case_audit(case_id: int, db: Session = Depends(get_db)):
    """The full explainable trail for one case: every step, in order."""
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.case_id == case_id)
        .order_by(AuditLog.timestamp)
        .all()
    )
    if not logs:
        raise HTTPException(status_code=404, detail="No audit trail for this case")
    return [{"step": l.step, "detail": l.detail, "timestamp": l.timestamp.isoformat()} for l in logs]


@app.get("/metrics")
def get_metrics(db: Session = Depends(get_db)):
    return compute_metrics(db)


# ---------------------------------------------------------------------------
# 4. MINIMAL DASHBOARD (no build step, just fetch() against the API above)
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Revenue Recovery Dashboard</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 1000px; margin: 40px auto; padding: 0 20px; background:#fafafa; }
  h1 { font-size: 22px; }
  .metrics { display: flex; gap: 16px; flex-wrap: wrap; margin: 20px 0; }
  .card { background: white; border: 1px solid #e5e5e5; border-radius: 8px; padding: 16px 20px; min-width: 160px; }
  .card .label { font-size: 12px; color: #888; text-transform: uppercase; }
  .card .value { font-size: 24px; font-weight: 600; margin-top: 4px; }
  button { background: #111; color: white; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; margin-right: 8px; }
  button:disabled { background: #777; cursor: wait; }
  #status { min-height: 20px; margin-top: 12px; color: #555; font-size: 13px; }
  #status.error { color: #b91c1c; }
  #status.ok { color: #166534; }
  table { width: 100%; border-collapse: collapse; margin-top: 20px; background: white; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #eee; font-size: 13px; }
  th { color: #888; font-weight: 500; }
  .status { padding: 2px 8px; border-radius: 10px; font-size: 11px; }
  .recovered { background: #dcfce7; color: #166534; }
  .escalated { background: #fef3c7; color: #92400e; }
  .stopped { background: #f3f4f6; color: #6b7280; }
  .detected, .diagnosed, .action_taken { background: #dbeafe; color: #1e40af; }
</style>
</head>
<body>
  <h1>AI Revenue Recovery -- Dashboard</h1>
  <div>
    <button id="seed-btn" type="button">Seed 100 synthetic cases</button>
    <button id="process-btn" type="button">Run pipeline pass</button>
    <button id="refresh-btn" type="button">Refresh</button>
  </div>
  <div id="status"></div>
  <div class="metrics" id="metrics"></div>
  <table id="cases-table">
    <thead><tr><th>ID</th><th>Type</th><th>Root Cause</th><th>Amount</th><th>Status</th><th>Retries</th><th>Audit</th></tr></thead>
    <tbody></tbody>
  </table>

<script>
const seedButton = document.getElementById('seed-btn');
const processButton = document.getElementById('process-btn');
const refreshButton = document.getElementById('refresh-btn');
const statusEl = document.getElementById('status');

seedButton.addEventListener('click', seedData);
processButton.addEventListener('click', runProcess);
refreshButton.addEventListener('click', refresh);

function setStatus(message = '', type = '') {
  statusEl.textContent = message;
  statusEl.className = type;
}

async function seedData() {
  seedButton.disabled = true;
  setStatus('Seeding synthetic cases...');
  try {
    const response = await fetch('/seed?count=100', { method: 'POST' });
    if (!response.ok) throw new Error(await response.text());
    const result = await response.json();
    setStatus(`Seeded ${result.created} synthetic cases.`, 'ok');
    await refresh();
  } catch (error) {
    setStatus(`Seed failed: ${error.message}`, 'error');
  } finally {
    seedButton.disabled = false;
  }
}
async function runProcess() {
  processButton.disabled = true;
  processButton.textContent = 'Running...';
  setStatus('Running pipeline pass. This can take a little while while cases are diagnosed.');
  try {
    const response = await fetch('/process', { method: 'POST' });
    if (!response.ok) throw new Error(await response.text());
    const result = await response.json();
    setStatus(`Pipeline pass complete. Processed ${result.processed} case(s).`, 'ok');
    await refresh();
  } catch (error) {
    setStatus(`Pipeline pass failed: ${error.message}`, 'error');
  } finally {
    processButton.disabled = false;
    processButton.textContent = 'Run pipeline pass';
  }
}
async function refresh() {
  refreshButton.disabled = true;
  try {
    const m = await (await fetch('/metrics')).json();
    document.getElementById('metrics').innerHTML = `
    <div class="card"><div class="label">Total Cases</div><div class="value">${m.total_cases}</div></div>
    <div class="card"><div class="label">At Risk</div><div class="value">₹${m.total_at_risk_amount.toLocaleString()}</div></div>
    <div class="card"><div class="label">Recovered</div><div class="value">₹${m.total_recovered_amount.toLocaleString()}</div></div>
    <div class="card"><div class="label">Recovery Rate</div><div class="value">${m.recovery_rate_by_amount_pct}%</div></div>
    <div class="card"><div class="label">Escalated</div><div class="value">${m.escalated_count}</div></div>
    <div class="card"><div class="label">Pending</div><div class="value">${m.pending_count}</div></div>
    `;
    const cases = await (await fetch('/cases?limit=50')).json();
    document.querySelector('#cases-table tbody').innerHTML = cases.map(c => `
    <tr>
      <td>${c.id}</td><td>${c.event_type}</td><td>${c.root_cause || '-'}</td>
      <td>₹${c.amount}</td><td><span class="status ${c.status}">${c.status}</span></td>
      <td>${c.retry_count}</td>
      <td><a href="/cases/${c.id}/audit" target="_blank">view</a></td>
    </tr>
    `).join('');
  } catch (error) {
    setStatus(`Refresh failed: ${error.message}`, 'error');
  } finally {
    refreshButton.disabled = false;
  }
}
refresh();
</script>
</body>
</html>
"""
