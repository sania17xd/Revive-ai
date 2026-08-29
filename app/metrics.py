"""
Computes the numbers every track's "bar" asks for: money at risk, money
recovered, recovery rate, and a breakdown by root cause. No AI here either
-- just aggregation over the Case table.
"""
from sqlalchemy import func
from app.models import Case


def compute_metrics(db) -> dict:
    total_cases = db.query(func.count(Case.id)).scalar() or 0
    total_at_risk = db.query(func.sum(Case.amount)).scalar() or 0.0
    total_recovered = db.query(func.sum(Case.recovered_amount)).scalar() or 0.0

    recovered_count = db.query(func.count(Case.id)).filter(Case.status == "recovered").scalar() or 0
    escalated_count = db.query(func.count(Case.id)).filter(Case.status == "escalated").scalar() or 0
    stopped_count = db.query(func.count(Case.id)).filter(Case.status == "stopped").scalar() or 0
    pending_count = db.query(func.count(Case.id)).filter(
        Case.status.in_(["detected", "diagnosed", "action_taken"])
    ).scalar() or 0

    by_root_cause = (
        db.query(Case.root_cause, func.count(Case.id), func.sum(Case.recovered_amount))
        .group_by(Case.root_cause)
        .all()
    )

    recovery_rate = (recovered_count / total_cases * 100) if total_cases else 0.0
    amount_recovery_rate = (total_recovered / total_at_risk * 100) if total_at_risk else 0.0

    return {
        "total_cases": total_cases,
        "total_at_risk_amount": round(total_at_risk, 2),
        "total_recovered_amount": round(total_recovered, 2),
        "recovery_rate_by_count_pct": round(recovery_rate, 2),
        "recovery_rate_by_amount_pct": round(amount_recovery_rate, 2),
        "recovered_count": recovered_count,
        "escalated_count": escalated_count,
        "stopped_count": stopped_count,
        "pending_count": pending_count,
        "breakdown_by_root_cause": [
            {"root_cause": rc or "not_yet_diagnosed", "cases": c, "amount_recovered": round(amt or 0.0, 2)}
            for rc, c, amt in by_root_cause
        ],
    }
