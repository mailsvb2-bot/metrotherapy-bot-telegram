from __future__ import annotations

from runtime import health_server
from services.db.schema.readiness import READY_TABLES


def _scheduler(**overrides):
    base = {
        "scheduler_loop_task_running": True,
        "scheduler_loop_started": True,
        "scheduler_loop_error_count": 0,
        "scheduler_loop_last_error": "",
        "scheduler_loop_last_error_age_sec": 0,
        "scheduler_loop_last_tick_age_sec": 0,
        "payment_retry_active": 0,
        "payment_retry_dead": 0,
    }
    base.update(overrides)
    return base


def test_payment_retry_table_is_required_for_readiness() -> None:
    assert "payment_reconciliation_retry" in READY_TABLES


def test_payment_retry_empty_queue_is_ready(monkeypatch) -> None:
    monkeypatch.delenv("PAYMENT_RETRY_READY_MAX_ACTIVE", raising=False)
    monkeypatch.delenv("PAYMENT_RETRY_READY_MAX_DEAD", raising=False)

    ready, errors, flags = health_server._scheduler_readiness(_scheduler())

    assert ready is True
    assert errors == []
    assert flags["payment_retry_unavailable"] is False
    assert flags["payment_retry_backlog"] is False
    assert flags["payment_retry_dead_letter"] is False


def test_payment_retry_dead_letter_fails_readiness_by_default(monkeypatch) -> None:
    monkeypatch.delenv("PAYMENT_RETRY_READY_MAX_DEAD", raising=False)

    ready, errors, flags = health_server._scheduler_readiness(
        _scheduler(payment_retry_dead=1)
    )

    assert ready is False
    assert "payment_retry:dead_letter" in errors
    assert flags["payment_retry_dead_letter"] is True
    assert flags["scheduler_degraded"] is True


def test_payment_retry_active_backlog_has_configurable_threshold(monkeypatch) -> None:
    monkeypatch.setenv("PAYMENT_RETRY_READY_MAX_ACTIVE", "2")

    ready, errors, flags = health_server._scheduler_readiness(
        _scheduler(payment_retry_active=3)
    )

    assert ready is False
    assert "payment_retry:backlog" in errors
    assert flags["payment_retry_backlog"] is True


def test_payment_retry_snapshot_failure_fails_readiness() -> None:
    ready, errors, flags = health_server._scheduler_readiness(
        _scheduler(payment_retry_active=-1, payment_retry_dead=-1)
    )

    assert ready is False
    assert "payment_retry:unavailable" in errors
    assert flags["payment_retry_unavailable"] is True


def test_payment_retry_dead_threshold_can_be_raised_for_controlled_recovery(monkeypatch) -> None:
    monkeypatch.setenv("PAYMENT_RETRY_READY_MAX_DEAD", "2")

    ready, errors, flags = health_server._scheduler_readiness(
        _scheduler(payment_retry_dead=2)
    )

    assert ready is True
    assert errors == []
    assert flags["payment_retry_dead_letter"] is False
