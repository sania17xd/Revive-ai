"""
Executes the action chosen by policy.decide_action(). For the hackathon,
notification-style actions are SIMULATED (logged, not actually sent) --
that's fine for demo purposes and keeps you from needing SMS/email infra.

The payment-link action is written so it's ready to call real Razorpay test
mode APIs once you have keys -- see razorpay_client.py.
"""
import random
import datetime
from app.models import AuditLog
from app.razorpay_client import create_payment_link, is_razorpay_configured


def execute_action(db, case, action: str, reason: str):
    """Runs `action` against `case`, updates case state, writes audit rows."""

    case.last_action = action
    _log(db, case, "decided", reason)

    if action == "escalate_to_human":
        case.status = "escalated"
        _log(db, case, "escalated", "Handed off to human review queue per policy.")

    elif action == "wait_for_cooldown":
        case.status = "action_taken"
        _log(db, case, "cooldown_wait", "No customer action taken; waiting for policy cooldown to expire.")

    elif action == "stop_and_log":
        case.status = "stopped"
        _log(db, case, "stopped", "Recovery attempts stopped per policy stopping rule. No further action.")

    elif action in ("immediate_retry", "wait_and_retry"):
        case.retry_count += 1
        outcome = _simulate_retry_outcome(case)
        _log(db, case, "action_executed", f"Retry attempt #{case.retry_count} executed ({action}).")
        _apply_outcome(db, case, outcome)

    elif action == "send_reminder":
        case.status = "action_taken"
        _log(db, case, "action_executed", f"Reminder message sent to customer {case.customer_id} (simulated).")

    elif action == "suggest_alternate_method":
        case.status = "action_taken"
        if is_razorpay_configured():
            link = create_payment_link(case)
            _log(db, case, "action_executed", f"New payment link created suggesting alternate method: {link}")
        else:
            _log(db, case, "action_executed",
                 "Simulated: new payment link suggesting an alternate payment method sent to customer. "
                 "(Set RAZORPAY_KEY_ID/SECRET to create a real test-mode payment link here.)")

    elif action == "send_update_payment_method_link":
        case.status = "action_taken"
        _log(db, case, "action_executed",
             f"Simulated: 'update your card' link sent to customer {case.customer_id}.")

    elif action in ("send_nudge", "send_nudge_with_incentive"):
        case.retry_count += 1
        incentive = " with a small discount incentive" if action == "send_nudge_with_incentive" else ""
        _log(db, case, "action_executed", f"Cart nudge{incentive} sent to customer {case.customer_id} (simulated).")
        outcome = _simulate_retry_outcome(case, base_rate=0.35)
        _apply_outcome(db, case, outcome)

    else:
        _log(db, case, "action_executed", f"Unrecognized action '{action}' -- no-op, escalating for safety.")
        case.status = "escalated"

    db.commit()


def _simulate_retry_outcome(case, base_rate: float = 0.45) -> bool:
    """
    Simulates whether a retry/nudge succeeds. In a real deployment this
    would come back from a webhook (payment.captured). For the hackathon,
    we roll weighted dice so your batch metrics look like a real funnel,
    not 100% or 0% recovery.
    """
    return random.random() < base_rate


def _apply_outcome(db, case, succeeded: bool):
    if succeeded:
        case.status = "recovered"
        case.recovered_amount = case.amount
        case.recovered_at = datetime.datetime.utcnow()
        _log(db, case, "outcome", f"Recovery succeeded. {case.amount} {case.currency} recovered.")
    else:
        _log(db, case, "outcome", "Attempt did not succeed. Will re-evaluate against policy on next pass.")
        if case.status != "escalated":
            case.status = "action_taken"


def _log(db, case, step: str, detail: str):
    entry = AuditLog(case_id=case.id, step=step, detail=detail)
    db.add(entry)
