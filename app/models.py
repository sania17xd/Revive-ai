"""
Two tables are all this project needs:

Case      -- one row per "thing that might be recoverable revenue"
            (a failed payment, an abandoned checkout, a failed subscription charge)
AuditLog  -- one row per step taken on a Case (detected, diagnosed, decided,
            action executed, outcome). This IS your audit trail requirement.
"""
import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Text, ForeignKey
)
from sqlalchemy.orm import relationship
from app.database import Base


class Case(Base):
    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, index=True)

    # --- where this event came from ---
    source = Column(String, default="synthetic")       # "synthetic" or "razorpay_test"
    event_type = Column(String, index=True)             # payment.failed | checkout.abandoned | subscription.failed
    order_id = Column(String, index=True)
    payment_id = Column(String, nullable=True)
    customer_id = Column(String, index=True)

    # --- money involved ---
    amount = Column(Float)
    currency = Column(String, default="INR")

    # --- raw signal used for diagnosis ---
    payment_method = Column(String, nullable=True)      # card | upi | netbanking | wallet
    failure_code = Column(String, nullable=True)        # e.g. INSUFFICIENT_FUNDS, BANK_DECLINE, TIMEOUT
    retry_count = Column(Integer, default=0)

    # --- pipeline state ---
    status = Column(String, default="detected", index=True)
    # detected -> diagnosed -> action_taken -> (recovered | escalated | stopped)

    # --- diagnosis output (filled by the LLM diagnosis call) ---
    root_cause = Column(String, nullable=True)
    diagnosis_confidence = Column(Float, nullable=True)
    diagnosis_reasoning = Column(Text, nullable=True)

    # --- decision + outcome ---
    last_action = Column(String, nullable=True)
    recovered_amount = Column(Float, default=0.0)
    recovered_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    audit_logs = relationship("AuditLog", back_populates="case", cascade="all, delete-orphan")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"), index=True)
    step = Column(String)     # detected | diagnosed | decided | action_executed | outcome | stopped | escalated
    detail = Column(Text)     # human-readable explanation of what happened and why
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)

    case = relationship("Case", back_populates="audit_logs")