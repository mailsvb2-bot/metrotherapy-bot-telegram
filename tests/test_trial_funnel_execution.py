from __future__ import annotations

import services.trial_funnel_execution as execution
from core.ai.decision_core import DecisionCore


def _outcome(quality: str, delta: int) -> dict[str, object]:
    return {
        "session_id": 1,
        "kind": "work",
        "pre": 0,
        "post": delta,
        "delta": delta,
        "quality": quality,
    }


def test_trial_sales_gate_does_not_apply_to_non_sales_job(monkeypatch):
    monkeypatch.setattr(
        execution,
        "trial_latest_outcome",
        lambda _user_id: (_ for _ in ()).throw(AssertionError("must not query outcome")),
    )

    gate = execution.trial_sales_job_gate(10, "demo_send")

    assert gate.applies is False
    assert gate.allow is True
    assert gate.reason == "not_trial_sales_job"


def test_trial_sales_gate_blocks_missing_outcome(monkeypatch):
    monkeypatch.setattr(execution, "trial_latest_outcome", lambda _user_id: None)

    gate = execution.trial_sales_job_gate(10, "funnel_offer")

    assert gate.applies is True
    assert gate.allow is False
    assert gate.reason == "trial_outcome_missing"
    assert gate.action == "ask_post_score"


def test_trial_sales_gate_blocks_negative_outcome(monkeypatch):
    monkeypatch.setattr(
        execution,
        "trial_latest_outcome",
        lambda _user_id: _outcome("negative", -3),
    )

    gate = execution.trial_sales_job_gate(10, "funnel2_demo_nopay_24h")

    assert gate.allow is False
    assert gate.reason == "trial_outcome_negative"
    assert gate.action == "safety_pause"
    assert gate.delta == -3


def test_trial_sales_gate_keeps_neutral_user_on_soft_immediate_path(monkeypatch):
    monkeypatch.setattr(
        execution,
        "trial_latest_outcome",
        lambda _user_id: _outcome("neutral", 0),
    )

    for job_type in (
        "funnel_nudge",
        "funnel_postdemo",
        "funnel_offer",
        "funnel2_demo_nopay_24h",
    ):
        gate = execution.trial_sales_job_gate(10, job_type)
        assert gate.allow is False
        assert gate.reason == "trial_neutral_soft_path_only"
        assert gate.action == "suggest_second_demo_soft"


def test_trial_sales_gate_allows_regular_offer_after_positive_outcome(monkeypatch):
    monkeypatch.setattr(
        execution,
        "trial_latest_outcome",
        lambda _user_id: _outcome("positive", 4),
    )

    gate = execution.trial_sales_job_gate(10, "funnel_offer")

    assert gate.allow is True
    assert gate.reason == "trial_positive_offer_allowed"
    assert gate.action == "continue_offer"
    assert gate.quality == "positive"
    assert gate.delta == 4


def test_trial_sales_gate_blocks_pressure_even_after_positive_outcome(monkeypatch):
    monkeypatch.setattr(
        execution,
        "trial_latest_outcome",
        lambda _user_id: _outcome("positive", 2),
    )

    for job_type in ("funnel_deadline", "funnel_lastcall"):
        gate = execution.trial_sales_job_gate(10, job_type)
        assert gate.allow is False
        assert gate.reason == "trial_pressure_step_blocked"


def test_decision_core_enforces_trial_gate_for_queued_sales_job(monkeypatch):
    monkeypatch.setattr(execution, "trial_latest_outcome", lambda _user_id: None)

    decision = DecisionCore.instance().decide(
        {
            "intent": "engine_job_execute",
            "job_type": "funnel_offer",
            "user_id": 0,
        }
    )

    assert decision.payload["type"] == "job_execution_denied"
    assert decision.payload["reason"] == "trial_outcome_missing"
    assert decision.payload["trial_gate"]["action"] == "ask_post_score"
    assert decision.meta["policy"] == "engine_job_registry_v1+trial_outcome_guard_v1"


def test_decision_core_allows_positive_regular_offer(monkeypatch):
    monkeypatch.setattr(
        execution,
        "trial_latest_outcome",
        lambda _user_id: _outcome("positive", 5),
    )

    decision = DecisionCore.instance().decide(
        {
            "intent": "engine_job_execute",
            "job_type": "funnel_postdemo",
            "user_id": 0,
        }
    )

    assert decision.payload["type"] == "job_execution_allowed"
    assert decision.payload["trial_gate"]["reason"] == "trial_positive_offer_allowed"
    assert decision.meta["policy"] == "engine_job_registry_v1+trial_outcome_guard_v1"
