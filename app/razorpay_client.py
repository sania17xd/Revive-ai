"""
Thin wrapper so the rest of the app doesn't crash if Razorpay keys aren't
set yet. Once you have test-mode keys in .env, create_payment_link() will
actually create a real (test-mode) payment link.
"""
import os
from dotenv import load_dotenv

load_dotenv()

KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")


def is_razorpay_configured() -> bool:
    return bool(KEY_ID) and bool(KEY_SECRET) and "xxxx" not in KEY_ID


def get_client():
    import razorpay
    return razorpay.Client(auth=(KEY_ID, KEY_SECRET))


def create_payment_link(case) -> str:
    """
    Creates a real Razorpay test-mode payment link for a recovery attempt.
    Returns the short URL, or a placeholder if Razorpay isn't configured.
    Docs: https://razorpay.com/docs/api/payments/payment-links/
    """
    if not is_razorpay_configured():
        return "https://rzp.io/l/simulated-link (Razorpay not configured)"

    client = get_client()
    link = client.payment_link.create({
        "amount": int(case.amount * 100),  # paise
        "currency": case.currency,
        "description": f"Recovery attempt for order {case.order_id}",
        "customer": {"contact": "", "name": case.customer_id},
        "notify": {"sms": False, "email": False},
        "reminder_enable": True,
        "notes": {"recovery_case_id": str(case.id), "root_cause": case.root_cause or ""},
    })
    return link.get("short_url", "unknown")
