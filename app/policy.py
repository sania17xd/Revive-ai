"""
The policy engine. Deliberately NOT an AI call -- this is what makes your
agent's money-moving actions explainable and bounded, which is the bar every
track sets. It's just a lookup table plus a couple of guard checks.

Each root cause maps to:
  - an ordered list of actions to try
  - max_retries: hard cap on automated attempts
  - cooldown_hours: minimum wait between actions
  - escalate_after: what to do once max_retries is hit (never "retry forever")
"""

POLICY_TABLE = {
    "insufficient_funds": {
        "actions": ["wait_and_retry", "send_reminder"],
        "max_retries": 2,
        "cooldown_hours": 24,
        "escalate_after": "stop_and_log",   # don't nag someone who can't pay
    },
    "bank_decline": {
        "actions": ["suggest_alternate_method", "send_reminder"],
        "max_retries": 2,
        "cooldown_hours": 6,
        "escalate_after": "stop_and_log",
    },
    "network_timeout": {
        "actions": ["immediate_retry"],
        "max_retries": 1,
        "cooldown_hours": 0,
        "escalate_after": "send_reminder",
    },
    "card_expired": {
        "actions": ["send_update_payment_method_link"],
        "max_retries": 1,
        "cooldown_hours": 12,
        "escalate_after": "stop_and_log",
    },
    "risk_block": {
        "actions": ["escalate_to_human"],   # never auto-retry a risk-blocked payment
        "max_retries": 0,
        "cooldown_hours": 0,
        "escalate_after": "escalate_to_human",
    },
    "user_abandoned": {
        "actions": ["send_nudge", "send_nudge_with_incentive"],
        "max_retries": 3,
        "cooldown_hours": 24,
        "escalate_after": "stop_and_log",
    },
    "unknown": {
        "actions": ["escalate_to_human"],
        "max_retries": 0,
        "cooldown_hours": 0,
        "escalate_after": "escalate_to_human",
    },
}


def decide_action(case) -> dict:
    """
    Given a Case (already diagnosed), decide what to do next.
    Returns {"action": str, "reason": str} -- always something explainable.
    This function is the single place that enforces bounds. No other code
    path should trigger a recovery action.
    """
    policy = POLICY_TABLE.get(case.root_cause, POLICY_TABLE["unknown"])

    if case.retry_count >= policy["max_retries"]:
        return {
            "action": policy["escalate_after"],
            "reason": (
                f"Retry cap reached ({case.retry_count}/{policy['max_retries']}) "
                f"for root cause '{case.root_cause}'. Escalating per policy instead "
                f"of retrying indefinitely."
            ),
        }

    action_index = min(case.retry_count, len(policy["actions"]) - 1)
    action = policy["actions"][action_index]

    return {
        "action": action,
        "reason": (
            f"Root cause '{case.root_cause}' (confidence {case.diagnosis_confidence:.2f}). "
            f"Attempt {case.retry_count + 1}/{policy['max_retries']}. "
            f"Policy selects '{action}'."
        ),
    }
