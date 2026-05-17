from __future__ import annotations

"""Outcome-aware policy for try-before-buy follow-up funnels.

The policy is intentionally pure: it receives the latest trial outcome and
returns an action. It does not send messages, create jobs, mutate DB, or
generate marketing text. Scheduled jobs and handlers can use this single surface
instead of growing their own trial/funnel branches.
"""

from dataclasses import dataclass
from typing import Any, Literal

TrialFunnelAction = Literal[
    "continue_offer",
    "ask_post_score",
    "suggest_second_demo_soft",
    "safety_pause",
]


@dataclass(frozen=True)
class TrialFunnelDecision:
    action: TrialFunnelAction
    reason: str
    quality: str | None = None
    delta: int | None = None
    allow_paid_cta: bool = False
    allow_pressure: bool = False


SALES_STEPS: frozenset[str] = frozenset(
    {
        "postdemo",
        "offer",
        "offer_nextday",
        "deadline",
        "lastcall",
        "demo_nopay_24h",
    }
)

PRESSURE_STEPS: frozenset[str] = frozenset({"deadline", "lastcall"})


def decide_trial_funnel_action(
    latest_outcome: dict[str, Any] | None,
    *,
    step: str,
) -> TrialFunnelDecision:
    """Return the canonical scheduled-funnel action for a trial user.

    Rules:
    - no completed outcome yet: do not sell first; ask for post-score/evidence;
    - negative outcome: safety pause, no paid CTA;
    - neutral outcome: soft second-demo/route exploration, no pressure;
    - positive outcome: paid CTA allowed, while pressure steps stay guarded.
    """

    step_norm = (step or "").strip().lower() or "unknown"
    if latest_outcome is None:
        return TrialFunnelDecision(
            action="ask_post_score",
            reason="trial_outcome_missing",
            allow_paid_cta=False,
            allow_pressure=False,
        )

    quality = str(latest_outcome.get("quality") or "").strip().lower() or None
    delta_raw = latest_outcome.get("delta")
    try:
        delta = int(delta_raw) if delta_raw is not None else None
    except (TypeError, ValueError):
        delta = None

    if quality == "negative" or (delta is not None and delta < 0):
        return TrialFunnelDecision(
            action="safety_pause",
            reason="trial_outcome_negative",
            quality="negative",
            delta=delta,
            allow_paid_cta=False,
            allow_pressure=False,
        )

    if quality == "neutral" or delta == 0:
        return TrialFunnelDecision(
            action="suggest_second_demo_soft",
            reason="trial_outcome_neutral",
            quality="neutral",
            delta=delta,
            allow_paid_cta=(step_norm not in PRESSURE_STEPS),
            allow_pressure=False,
        )

    if quality == "positive" or (delta is not None and delta > 0):
        return TrialFunnelDecision(
            action="continue_offer",
            reason="trial_outcome_positive",
            quality="positive",
            delta=delta,
            allow_paid_cta=True,
            allow_pressure=(step_norm not in PRESSURE_STEPS),
        )

    return TrialFunnelDecision(
        action="ask_post_score",
        reason="trial_outcome_unknown",
        quality=quality,
        delta=delta,
        allow_paid_cta=False,
        allow_pressure=False,
    )


def should_send_sales_followup(
    latest_outcome: dict[str, Any] | None,
    *,
    step: str,
) -> bool:
    """Return whether a scheduled selling follow-up may be sent."""

    step_norm = (step or "").strip().lower() or "unknown"
    if step_norm not in SALES_STEPS:
        return True
    decision = decide_trial_funnel_action(latest_outcome, step=step_norm)
    if step_norm in PRESSURE_STEPS:
        return bool(decision.allow_pressure)
    return bool(decision.allow_paid_cta)
