"""
Run with:  uvicorn app.main:app --reload
Then open: http://localhost:8000/docs   (interactive API playground)
           http://localhost:8000/dashboard

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

app = FastAPI(title="Revive AI — Revenue Recovery Agent")

RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")


# ---------------------------------------------------------------------------
# 1. INGESTION -- synthetic data OR real Razorpay webhooks


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
        failure_code=entity.get("error_reason") or entity.get("error_code"),
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
        .filter(Case.status.in_(["detected", "diagnosed", "action_taken"]))
        .order_by(desc(Case.created_at), desc(Case.id))
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

        last_action = (
            db.query(AuditLog)
            .filter(AuditLog.case_id == case.id, AuditLog.step == "action_executed")
            .order_by(desc(AuditLog.timestamp))
            .first()
        )
        decision = decide_action(case, last_action_at=last_action.timestamp if last_action else None)
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
            "last_action": c.last_action,
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
        "last_action": case.last_action,
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
# 4. DASHBOARD (no build step, just fetch() against the API above)
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Revive AI -- Dashboard</title>
<style>
  :root {
    --blue: #0b72e7;
    --blue-dark: #072654;
    --blue-soft: #eaf3ff;
    --blue-line: #bfd7f7;
    --white: #ffffff;
    --text: #172033;
    --muted: #667085;
  }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    max-width: 1100px;
    margin: 0 auto;
    padding: 40px 20px;
    color: var(--text);
    background: linear-gradient(180deg, #eef6ff 0, #ffffff 260px);
  }
  h1 { margin: 0 0 20px; color: var(--blue-dark); font-size: 28px; }
  .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin: 28px 0; }
  .card {
    background: var(--white);
    border: 1px solid var(--blue-line);
    border-left: 5px solid var(--blue);
    border-radius: 8px;
    padding: 18px 20px;
    min-width: 160px;
    box-shadow: 0 8px 22px rgba(11, 114, 231, .08);
  }
  .card .label { font-size: 12px; color: var(--muted); text-transform: uppercase; }
  .card .value { color: var(--blue-dark); font-size: 28px; font-weight: 700; margin-top: 6px; }
  button {
    background: var(--blue);
    color: white;
    border: 1px solid var(--blue);
    padding: 10px 16px;
    border-radius: 6px;
    cursor: pointer;
    margin-right: 8px;
    font-weight: 600;
  }
  button:hover { background: #075fc4; border-color: #075fc4; }
  button:disabled { background: #88addb; border-color: #88addb; cursor: wait; }
  #status { min-height: 20px; margin-top: 14px; color: var(--muted); font-size: 13px; }
  #status.error { color: #b42318; }
  #status.ok { color: var(--blue); }
  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 20px;
    background: white;
    border: 1px solid var(--blue-line);
    box-shadow: 0 8px 22px rgba(11, 114, 231, .06);
  }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #e6eef9; font-size: 13px; }
  th { color: var(--blue-dark); font-weight: 700; background: var(--blue-soft); }
  tbody tr:hover { background: #f6fbff; }
  a { color: var(--blue); font-weight: 600; text-decoration: none; }
  .status {
    padding: 3px 9px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 700;
    background: var(--blue-soft);
    color: var(--blue-dark);
  }
  .recovered { background: #dcfce7; color: #166534; }
  .escalated { background: #fef3c7; color: #92400e; }
  .stopped { background: #f3f4f6; color: #6b7280; }
  .detected, .diagnosed, .action_taken { background: #dbeafe; color: #1e40af; }
</style>
</head>
<body>
  <h1>Revive AI -- Revenue Recovery Dashboard</h1>
  <div>
    <button id="seed-btn" type="button">Seed 100 synthetic cases</button>
    <button id="process-btn" type="button">Run pipeline pass</button>
    <button id="refresh-btn" type="button">Refresh</button>
  </div>
  <div id="status"></div>
  <div class="metrics" id="metrics"></div>
  <table id="cases-table">
    <thead><tr><th>ID</th><th>Type</th><th>Root Cause</th><th>Amount</th><th>Status</th><th>Action Taken</th><th>Recovered</th><th>Retries</th><th>Audit</th></tr></thead>
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
      <td>${c.last_action || '-'}</td>
      <td>${c.recovered_amount > 0 ? '₹' + c.recovered_amount : '-'}</td>
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
