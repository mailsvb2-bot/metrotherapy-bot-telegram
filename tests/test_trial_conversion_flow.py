from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace

import pytest

from core.ai.decision_core import DecisionCore
from core.time_utils import utc_now
from services.db import db
from services.jobs import add_job
from services.mood import create_session, set_post, set_pre
import services.trial_conversion_flow as conversion
from services.trial_conversion_flow import plan_trial_conversion_after_outcome


def _completed_session(
    user_id: int,
    *,
    source: str = "demo",
    pre: int = 0,
    post: int = 3,
    kind: str = "work",
) -> int:
    sid = create_session(
        int(user_id),
        kind=kind,
        source=source,
        day="2026-08-12",
        anchor_id=1,
    )
    assert set_pre(sid, pre) is True
    assert set_post(sid, post) is True
    return sid


def _pending_sales_jobs(user_id: int):
    with db() as conn:
        return conn.execute(
            """
            SELECT job_type, job_key, payload
            FROM jobs
            WHERE user_id=? AND done_at IS NULL
              AND job_type IN (
                'funnel_nudge',
                'funnel_postdemo',
                'funnel_offer',
                'funnel_deadline',
                'funnel_lastcall',
                'funnel2_demo_nopay_24h'
              )
            ORDER BY job_type, job_key
            """.strip(),
            (int(user_id),),
        ).fetchall()


def test_decision_core_owns_trial_outcome_conversion_policy() -> None:
    decision = DecisionCore.instance().decide(
        {
            "intent": "trial_outcome_conversion",
            "user_id": -996000,
            "source": "vk",
            "trial_outcome": {"quality": "positive", "delta": 4},
        }
    )

    assert decision.payload["type"] == "trial_conversion_decision"
    assert decision.payload["action"] == "continue_offer"
    assert decision.payload["allow_paid_cta"] is True
    assert decision.payload["allow_pressure"] is True
    assert decision.meta["policy"] == "trial_outcome_policy_v1"


def test_positive_demo_outcome_plans_soft_followups_idempotently() -> None:
    user_id = -996001
    sid = _completed_session(user_id, pre=-1, post=3)

    first = plan_trial_conversion_after_outcome(user_id, sid, platform="vk")

    assert first is not None
    assert first.quality == "positive"
    assert first.delta == 4
    assert first.action == "continue_offer"
    assert first.allow_paid_cta is True

    rows = _pending_sales_jobs(user_id)
    assert Counter(str(row["job_type"]) for row in rows) == Counter(
        {
            "funnel_postdemo": 1,
            "funnel_offer": 2,
            "funnel_nudge": 1,
            "funnel2_demo_nopay_24h": 1,
        }
    )
    assert all("trial-outcome:" in str(row["job_key"]) for row in rows)
    assert all(str(sid) in str(row["job_key"]) for row in rows)

    second = plan_trial_conversion_after_outcome(user_id, sid, platform="vk")
    assert second is not None
    assert len(_pending_sales_jobs(user_id)) == 5


def test_neutral_demo_outcome_keeps_soft_cta_without_pressure() -> None:
    user_id = -996002
    sid = _completed_session(user_id, pre=2, post=2, kind="home")

    plan = plan_trial_conversion_after_outcome(user_id, sid, platform="max")

    assert plan is not None
    assert plan.quality == "neutral"
    assert plan.action == "suggest_second_demo_soft"
    assert plan.allow_paid_cta is True
    assert plan.allow_pressure is False
    assert "второй бесплатный маршрут" in plan.message
    jobs = _pending_sales_jobs(user_id)
    assert len(jobs) == 5
    assert not any(str(row["job_type"]) in {"funnel_deadline", "funnel_lastcall"} for row in jobs)


def test_negative_demo_outcome_cancels_legacy_sales_and_plans_none() -> None:
    user_id = -996003
    sid = _completed_session(user_id, pre=3, post=-1)
    assert add_job(
        user_id,
        "funnel_offer",
        (utc_now() + timedelta(hours=1)).isoformat(),
        {"kind": "work", "variant": "legacy-pre-outcome"},
    ) is True
    assert len(_pending_sales_jobs(user_id)) == 1

    plan = plan_trial_conversion_after_outcome(user_id, sid, platform="telegram")

    assert plan is not None
    assert plan.quality == "negative"
    assert plan.action == "safety_pause"
    assert plan.allow_paid_cta is False
    assert plan.allow_pressure is False
    assert _pending_sales_jobs(user_id) == []


def test_non_demo_session_never_enters_trial_conversion() -> None:
    user_id = -996004
    sid = _completed_session(user_id, source="settings", pre=0, post=5)

    plan = plan_trial_conversion_after_outcome(user_id, sid, platform="telegram")

    assert plan is None
    assert _pending_sales_jobs(user_id) == []


def test_incomplete_demo_outcome_never_schedules_sales() -> None:
    user_id = -996005
    sid = create_session(
        user_id,
        kind="work",
        source="demo",
        day="2026-08-12",
        anchor_id=1,
    )
    assert set_pre(sid, 1) is True

    plan = plan_trial_conversion_after_outcome(user_id, sid, platform="vk")

    assert plan is None
    assert _pending_sales_jobs(user_id) == []


def test_trial_conversion_score_format_and_owner_guard() -> None:
    assert conversion._fmt_score(None) == "—"
    assert conversion._fmt_score(0) == "0"
    assert conversion._fmt_score(3) == "+3"

    owner_id = -996006
    sid = _completed_session(owner_id, pre=0, post=1)
    assert plan_trial_conversion_after_outcome(-996106, sid, platform="vk") is None
    assert _pending_sales_jobs(owner_id) == []


def test_trial_conversion_fails_closed_on_unexpected_decision_core_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = -996007
    sid = _completed_session(user_id, pre=0, post=4)

    monkeypatch.setattr(
        DecisionCore,
        "decide",
        lambda self, _world: SimpleNamespace(payload={"type": "noop"}),
    )

    plan = plan_trial_conversion_after_outcome(user_id, sid, platform="max")

    assert plan is not None
    assert plan.action == "safety_pause"
    assert plan.allow_paid_cta is False
    assert plan.allow_pressure is False
    assert "не добавлять коммерческих шагов" in plan.message
    assert _pending_sales_jobs(user_id) == []


@pytest.mark.parametrize(
    "error",
    [sqlite3.OperationalError("comparison unavailable"), ValueError("bad average")],
)
def test_trial_conversion_comparison_failure_does_not_break_saved_outcome(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    user_id = -996008 if isinstance(error, sqlite3.Error) else -996009
    sid = _completed_session(user_id, pre=-2, post=1)

    monkeypatch.setattr(
        conversion,
        "last_delta",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    plan = plan_trial_conversion_after_outcome(user_id, sid, platform="telegram")

    assert plan is not None
    assert plan.quality == "positive"
    assert plan.delta == 3
    assert plan.allow_paid_cta is True
    assert len(_pending_sales_jobs(user_id)) == 5


def test_trial_conversion_followup_failure_does_not_break_saved_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = -996010
    sid = _completed_session(user_id, pre=0, post=2)

    monkeypatch.setattr(
        conversion,
        "_schedule_followups",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("queue unavailable")),
    )

    plan = plan_trial_conversion_after_outcome(user_id, sid, platform="vk")

    assert plan is not None
    assert plan.quality == "positive"
    assert plan.allow_paid_cta is True
    assert _pending_sales_jobs(user_id) == []
