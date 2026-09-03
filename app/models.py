"""
Two tables are all this project needs:

Case      -- one row per "thing that might be recoverable revenue"
            (a failed payment, an abandoned checkout, a failed subscription charge)
AuditLog  -- one row per step taken on a Case (detected, diagnosed, decided,
            action executed, outcome). This IS your audit trail requirement.
"""
import datetime
from sqlalchemy import Integer, String, Float, DateTime, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # --- where this event came from ---
    source: Mapped[str] = mapped_column(String, default="synthetic")       # "synthetic" or "razorpay_test"
    event_type: Mapped[str] = mapped_column(String, index=True)             # payment.failed | checkout.abandoned | subscription.failed
    order_id: Mapped[str] = mapped_column(String, index=True)
    payment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    customer_id: Mapped[str] = mapped_column(String, index=True)

    # --- money involved ---
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String, default="INR")

    # --- raw signal used for diagnosis ---
    payment_method: Mapped[str | None] = mapped_column(String, nullable=True)      # card | upi | netbanking | wallet
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)        # e.g. INSUFFICIENT_FUNDS, BANK_DECLINE, TIMEOUT
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    # --- pipeline state ---
    status: Mapped[str] = mapped_column(String, default="detected", index=True)
    # detected -> diagnosed -> action_taken -> (recovered | escalated | stopped)

    # --- diagnosis output (filled by the LLM diagnosis call) ---
    root_cause: Mapped[str | None] = mapped_column(String, nullable=True)
    diagnosis_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    diagnosis_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- decision + outcome ---
    last_action: Mapped[str | None] = mapped_column(String, nullable=True)
    recovered_amount: Mapped[float] = mapped_column(Float, default=0.0)
    recovered_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    audit_logs: Mapped[list["AuditLog"]] = relationship("AuditLog", back_populates="case", cascade="all, delete-orphan")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"), index=True)
    step: Mapped[str] = mapped_column(String)     # detected | diagnosed | decided | action_executed | outcome | stopped | escalated
    detail: Mapped[str] = mapped_column(Text)     # human-readable explanation of what happened and why
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

    case: Mapped["Case"] = relationship("Case", back_populates="audit_logs")
