from __future__ import annotations

"""Execution-time guard for scheduled trial sales jobs.

The scheduler remains responsible only for timing/idempotency.  This module
bridges the canonical persisted trial outcome with the pure trial funnel policy
at the last safe point before a selling job is executed.  It intentionally does
not send messages, mutate jobs, or create another funnel state machine.
"""

from dataclasses import dataclass

from services.trial_analytics import trial_latest_outcome
from services.trial_funnel_policy import decide_trial_funnel_action


TRIAL_SALES_JOB_STEPS: dict[str, str] = {
    "funnel_nudge": "nudge",
    "funnel_postdemo": "postdemo",
    "funnel_offer": "offer",
    "funnel_deadline": "deadline",
    "funnel_lastcall": "lastcall",
    "funnel2_demo_nopay_24h": "demo_nopay_24h",
}

PRESSURE_JOB_TYPES = frozenset({"funnel_deadline", "funnel_lastcall"})


@dataclass(frozen=True)
class TrialSalesJobGate:
    applies: bool
    allow: bool
    reason: str
    job_type: str
    step: str | None = None
    action: str | None = None
    quality: str | None = None
    delta: int | None = None

    def audit_payload(self) -> dict[str, object]:
        return {
            "applies": self.applies,
            "allow": self.allow,
            "reason": self.reason,
            "job_type": self.job_type,
            "step": self.step,
            "action": self.action,
            "quality": self.quality,
            "delta": self.delta,
        }


def is_trial_sales_job(job_type: str | None) -> bool:
    return str(job_type or "").strip() in TRIAL_SALES_JOB_STEPS


def trial_sales_job_gate(user_id: int, job_type: str | None) -> TrialSalesJobGate:
    """Return the canonical execution gate for one scheduled trial sales job.

    Existing jobs may have been scheduled before the user supplied a post-demo
    score. Reading the latest outcome at execution time means queued jobs are
    always governed by the newest evidence without inventing another state.

    Runtime contract:
    - no completed outcome -> no selling follow-up; wait for outcome evidence;
    - negative outcome -> safety pause, no paid CTA;
    - neutral outcome -> soft non-pressure CTA may execute;
    - positive outcome -> regular non-pressure CTA may execute;
    - deadline/lastcall pressure jobs remain blocked whenever policy disallows
      pressure.
    """

    normalized_job = str(job_type or "").strip()
    step = TRIAL_SALES_JOB_STEPS.get(normalized_job)
    if step is None:
        return TrialSalesJobGate(
            applies=False,
            allow=True,
            reason="not_trial_sales_job",
            job_type=normalized_job,
        )

    outcome = trial_latest_outcome(int(user_id))
    decision = decide_trial_funnel_action(outcome, step=step)

    if decision.action == "ask_post_score":
        return TrialSalesJobGate(
            applies=True,
            allow=False,
            reason="trial_outcome_missing",
            job_type=normalized_job,
            step=step,
            action=decision.action,
            quality=decision.quality,
            delta=decision.delta,
        )

    if decision.action == "safety_pause":
        return TrialSalesJobGate(
            applies=True,
            allow=False,
            reason="trial_outcome_negative",
            job_type=normalized_job,
            step=step,
            action=decision.action,
            quality=decision.quality,
            delta=decision.delta,
        )

    if normalized_job in PRESSURE_JOB_TYPES and not decision.allow_pressure:
        return TrialSalesJobGate(
            applies=True,
            allow=False,
            reason="trial_pressure_step_blocked",
            job_type=normalized_job,
            step=step,
            action=decision.action,
            quality=decision.quality,
            delta=decision.delta,
        )

    if not decision.allow_paid_cta:
        return TrialSalesJobGate(
            applies=True,
            allow=False,
            reason="trial_paid_cta_blocked",
            job_type=normalized_job,
            step=step,
            action=decision.action,
            quality=decision.quality,
            delta=decision.delta,
        )

    return TrialSalesJobGate(
        applies=True,
        allow=True,
        reason="trial_offer_allowed",
        job_type=normalized_job,
        step=step,
        action=decision.action,
        quality=decision.quality,
        delta=decision.delta,
    )
