"""
Generates realistic-looking failed-payment / abandoned-checkout events so
you can build and demo the whole pipeline before you ever touch Razorpay.
Run via POST /seed?count=100 once the server is up.
"""
import random
import uuid
from app.models import Case, AuditLog

FAILURE_CODES = [
    "INSUFFICIENT_FUNDS", "BANK_DECLINE", "TIMEOUT",
    "CARD_EXPIRED", "RISK_BLOCKED", None,  # None = abandoned checkout, no attempt
]
PAYMENT_METHODS = ["card", "upi", "netbanking", "wallet"]

# roughly maps failure codes to event types, so the data isn't pure noise
def _pick_event():
    code = random.choice(FAILURE_CODES)
    if code is None:
        return "checkout.abandoned", None
    if random.random() < 0.1:
        return "subscription.failed", code
    return "payment.failed", code


def generate_synthetic_cases(db, count: int = 100):
    created = []
    for _ in range(count):
        event_type, failure_code = _pick_event()
        amount = round(random.uniform(199, 15000), 2)

        case = Case(
            source="synthetic",
            event_type=event_type,
            order_id=f"order_{uuid.uuid4().hex[:10]}",
            payment_id=f"pay_{uuid.uuid4().hex[:10]}" if failure_code else None,
            customer_id=f"cust_{uuid.uuid4().hex[:8]}",
            amount=amount,
            currency="INR",
            payment_method=random.choice(PAYMENT_METHODS) if failure_code else None,
            failure_code=failure_code,
            retry_count=0,
            status="detected",
        )
        db.add(case)
        db.flush()  # get case.id before commit

        log = AuditLog(
            case_id=case.id,
            step="detected",
            detail=f"Synthetic event generated: {event_type}, failure_code={failure_code}, amount={amount}",
        )
        db.add(log)
        created.append(case)

    db.commit()
    return created
