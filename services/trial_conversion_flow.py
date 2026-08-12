from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from psycopg import Error as PsycopgError

from config.settings import settings
from core.time_utils import utc_now
from services.events import log_runtime_event
from services.jobs import add_job, cancel_jobs
from services.mood import get_session, last_delta
from services.trial_funnel_execution import TRIAL_SALES_JOB_STEPS
from services.trial_funnel_policy import decide_trial_funnel_action

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrialConversionPlan:
    user_id: int
    session_id: int
    kind: str
    quality: str
    delta: int
    action: str
    allow_paid_cta: bool
    allow_pressure: bool
    message: str


def _fmt_score(value: int | None) -> str:
    if value is None:
        return "—"
    parsed = int(value)
    return f"{parsed:+d}" if parsed != 0 else "0"


def _quality(delta: int) -> str:
    if int(delta) > 0:
        return "positive"
    if int(delta) < 0:
        return "negative"
    return "neutral"


def _outcome_text(
    *,
    pre: int,
    post: int,
    delta: int,
    avg_delta: int | None,
) -> str:
    base = (
        "✅ Зафиксировал состояние после демо-практики.\n\n"
        f"Сегодня: {_fmt_score(pre)} → {_fmt_score(post)} "
        f"(изменение {_fmt_score(delta)})"
    )
    if avg_delta is not None:
        base += f"\nСредняя динамика за последние дни: {_fmt_score(avg_delta)}"

    if delta > 0:
        return base + (
            "\n\nЭто хороший сигнал: формат Вам подходит. Одна практика может дать "
            "сдвиг, но главный эффект Метротерапии — в регулярном ритме: утро, "
            "вечер или оба маршрута.\n\nМожно открыть полный маршрут и продолжить "
            "уже не вслепую, а отталкиваясь от Вашего первого результата."
        )
    if delta == 0:
        return base + (
            "\n\nЯвного сдвига пока нет — это нормально. Иногда человеку лучше подходит "
            "другой момент дня: утро/дорога или вечер/домой.\n\nМожно попробовать "
            "второй бесплатный маршрут или спокойно посмотреть, что входит в полный маршрут."
        )
    return base + (
        "\n\nЯ вижу, что по Вашей оценке после практики стало тяжелее. Сейчас лучше "
        "не усиливать нагрузку и не торопиться с продолжением.\n\nСделайте паузу. "
        "Если состояние острое или небезопасное — обратитесь за живой профессиональной "
        "помощью. К практике можно вернуться позже, в более мягком темпе."
    )


def _job_key(user_id: int, session_id: int, suffix: str) -> str:
    return f"trial-outcome:{int(user_id)}:{int(session_id)}:{str(suffix)}"


def _schedule_followups(plan: TrialConversionPlan, *, platform: str) -> int:
    """Replace legacy pre-outcome sales jobs with one outcome-owned sequence."""

    cancel_jobs(int(plan.user_id), job_types=list(TRIAL_SALES_JOB_STEPS))
    if not plan.allow_paid_cta:
        return 0

    now = utc_now().replace(microsecond=0)
    postdemo_minutes = max(1, int(settings.FUNNEL_POSTDEMO_MINUTES))
    base_payload = {
        "kind": plan.kind,
        "outcome_session_id": int(plan.session_id),
        "outcome_quality": plan.quality,
        "outcome_delta": int(plan.delta),
        "source": "trial_outcome",
    }
    jobs = (
        (
            "funnel_postdemo",
            now + timedelta(minutes=postdemo_minutes),
            {**base_payload, "ack_at_utc": now.isoformat()},
            "postdemo",
        ),
        (
            "funnel_offer",
            now + timedelta(minutes=20),
            {**base_payload, "variant": "after_20m"},
            "offer-20m",
        ),
        (
            "funnel_nudge",
            now + timedelta(minutes=120),
            base_payload,
            "nudge-120m",
        ),
        (
            "funnel_offer",
            now + timedelta(days=1),
            {**base_payload, "variant": "nextday_same_time"},
            "offer-nextday",
        ),
        (
            "funnel2_demo_nopay_24h",
            now + timedelta(days=1),
            {**base_payload, "ack_at_utc": now.isoformat()},
            "nopay-24h",
        ),
    )

    created = 0
    for job_type, run_at, payload, suffix in jobs:
        if add_job(
            int(plan.user_id),
            job_type,
            run_at.isoformat(),
            payload,
            job_key=_job_key(plan.user_id, plan.session_id, suffix),
        ):
            created += 1

    log_runtime_event(
        int(plan.user_id),
        event_type="trial_conversion_followups_planned",
        source=str(platform or "messenger"),
        payload={
            "session_id": int(plan.session_id),
            "kind": plan.kind,
            "quality": plan.quality,
            "delta": int(plan.delta),
            "action": plan.action,
            "allow_paid_cta": bool(plan.allow_paid_cta),
            "jobs_created": created,
        },
    )
    return created


def plan_trial_conversion_after_outcome(
    user_id: int,
    session_id: int,
    *,
    platform: str,
) -> TrialConversionPlan | None:
    """Build and persist the canonical post-demo conversion plan.

    `mood_sessions` remains the source of outcome truth.  This function is the
    single bridge from completed demo evidence to commercial follow-up.  It is
    safe to call from any messenger adapter; non-demo or incomplete sessions are
    ignored.
    """

    session = get_session(int(session_id))
    if session is None or int(session.user_id) != int(user_id):
        return None
    if str(session.source or "") != "demo":
        return None
    if session.pre_score is None or session.post_score is None:
        return None

    pre = int(session.pre_score)
    post = int(session.post_score)
    delta = post - pre
    quality = _quality(delta)
    latest = {"quality": quality, "delta": delta}
    decision = decide_trial_funnel_action(latest, step="postdemo")
    comparison = last_delta(int(user_id), str(session.kind or ""))
    average_delta = comparison.get("avg_delta")

    plan = TrialConversionPlan(
        user_id=int(user_id),
        session_id=int(session_id),
        kind=str(session.kind or ""),
        quality=quality,
        delta=delta,
        action=decision.action,
        allow_paid_cta=bool(decision.allow_paid_cta),
        allow_pressure=bool(decision.allow_pressure),
        message=_outcome_text(
            pre=pre,
            post=post,
            delta=delta,
            avg_delta=int(average_delta) if average_delta is not None else None,
        ),
    )

    log_runtime_event(
        int(user_id),
        event_type="trial_outcome_recorded",
        source=str(platform or "messenger"),
        payload={
            "session_id": int(session_id),
            "kind": plan.kind,
            "quality": plan.quality,
            "delta": int(plan.delta),
            "action": plan.action,
            "allow_paid_cta": bool(plan.allow_paid_cta),
        },
    )
    log_runtime_event(
        int(user_id),
        event_type=f"trial_delta_{plan.quality}",
        source=str(platform or "messenger"),
        payload={
            "session_id": int(session_id),
            "kind": plan.kind,
            "delta": int(plan.delta),
        },
    )

    try:
        _schedule_followups(plan, platform=platform)
    except (sqlite3.Error, PsycopgError, RuntimeError, TypeError, ValueError):
        # The user's POST score is already canonical.  A commercial scheduling
        # failure must never turn a successfully completed practice into a UX
        # failure; readiness/observability will surface the scheduling error.
        log.exception(
            "trial conversion follow-up scheduling failed user_id=%s session_id=%s",
            int(user_id),
            int(session_id),
        )
        log_runtime_event(
            int(user_id),
            event_type="trial_conversion_followups_error",
            source=str(platform or "messenger"),
            payload={
                "session_id": int(session_id),
                "kind": plan.kind,
                "quality": plan.quality,
            },
        )

    return plan


__all__ = ["TrialConversionPlan", "plan_trial_conversion_after_outcome"]
