"""
This file is the entire "AI" part of the project. It's one function that:
  1. Builds a prompt describing the failed/abandoned case
  2. Asks Groq (free tier, no card needed, OpenAI-compatible) to classify
     it into a FIXED list of root causes
  3. Parses the JSON response

That's it -- no training, no fine-tuning, no agent frameworks needed.
Uses plain `requests` against Groq's OpenAI-compatible endpoint, so
there's no extra SDK to install or version-conflict with.
"""
import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_MODEL = "openai/gpt-oss-120b"  # current general-purpose model on Groq's free tier (llama-3.3-70b-versatile was deprecated June 2026)
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _get_api_key():
    """Returns the Groq API key from .env, or None if not configured yet
    -- callers must handle None gracefully rather than crashing."""
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key or "xxxx" in api_key:
        return None
    return api_key

# This list MUST match the keys in policy.POLICY_TABLE, or decide_action()
# will silently fall back to "unknown".
VALID_ROOT_CAUSES = [
    "insufficient_funds",
    "bank_decline",
    "network_timeout",
    "card_expired",
    "risk_block",
    "user_abandoned",
    "unknown",
]

DIAGNOSIS_SYSTEM_PROMPT = f"""You are a payment-failure diagnosis assistant for an
e-commerce revenue recovery system. Given details about a failed payment or
abandoned checkout, classify the root cause into EXACTLY ONE of these categories:

{", ".join(VALID_ROOT_CAUSES)}

Respond with ONLY a JSON object, no other text, no markdown fences:
{{"root_cause": "<one of the categories above>", "confidence": <float 0.0-1.0>, "reasoning": "<one sentence, plain English>"}}
"""


def diagnose_case(case) -> dict:
    """
    Takes a Case object, returns {"root_cause": str, "confidence": float, "reasoning": str}.
    Falls back to "unknown" with confidence 0.0 if the API call fails, the
    key isn't set, or the response can't be parsed -- we never want a
    broken API call to silently crash the whole pipeline.
    """
    api_key = _get_api_key()
    if api_key is None:
        return {
            "root_cause": "unknown",
            "confidence": 0.0,
            "reasoning": "GROQ_API_KEY not set in .env -- add your free key from console.groq.com.",
        }

    event_summary = f"""
Event type: {case.event_type}
Payment method: {case.payment_method or "not provided"}
Failure code from payment gateway: {case.failure_code or "none (checkout was abandoned, no attempt made)"}
Amount: {case.amount} {case.currency}
Previous retry attempts on this case: {case.retry_count}
"""

    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "temperature": 0.2,
                "max_tokens": 300,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": DIAGNOSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": event_summary},
                ],
            },
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        raw_text = data["choices"][0]["message"]["content"].strip()
        raw_text = raw_text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(raw_text)

        if parsed.get("root_cause") not in VALID_ROOT_CAUSES:
            parsed["root_cause"] = "unknown"

        return {
            "root_cause": parsed["root_cause"],
            "confidence": float(parsed.get("confidence", 0.5)),
            "reasoning": parsed.get("reasoning", "No reasoning provided."),
        }

    except requests.exceptions.HTTPError as e:
        # Surface Groq's actual error message (e.g. "model_decommissioned",
        # bad auth, etc.) instead of a generic HTTP status -- makes the
        # audit trail actually useful for debugging.
        try:
            server_message = e.response.json().get("error", {}).get("message", e.response.text)
        except Exception:
            server_message = e.response.text if e.response is not None else str(e)
        return {
            "root_cause": "unknown",
            "confidence": 0.0,
            "reasoning": f"Diagnosis failed, defaulting to manual review. Groq error: {server_message}",
        }
    except Exception as e:
        return {
            "root_cause": "unknown",
            "confidence": 0.0,
            "reasoning": f"Diagnosis failed, defaulting to manual review. Error: {e}",
        }
