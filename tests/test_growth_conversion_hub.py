from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services import growth_conversion_hub
from services.growth_conversion_hub_core import build_dry_run_conversion
from services.migrations.growth_conversion_outbox_v1 import apply as apply_growth_conversion_outbox_v1
from services.payments.reconciliation import ReconciliationResult


class _DbCtx:
    def __init__(self, path: Path):
        self.path = path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        assert self.conn is not None
        if exc_type is None:
            self.conn.commit()
        self.conn.close()
        return False


def _fake_db(path: Path):
    return _DbCtx(path)


def _prepare(path: Path) -> None:
    with _fake_db(path) as conn:
        apply_growth_conversion_outbox_v1(conn)


def test_dry_run_conversion_core_hard_locks_dispatch():
    item = build_dry_run_conversion(
        conversion_type="payment.succeeded",
        source_platform="YooKassa",
        source_event="payment.succeeded",
        external_event_id="pay_1",
        user_id=123,
        amount_minor=190000,
        currency="rub",
        attribution={"utm_source": "telegram_ads", "utm_campaign": "may"},
        payload={"package_id": "p10"},
        target_provider="yandex_ads",
    )

    assert item["conversion_type"] == "payment_success"
    assert item["mode"] == "dry_run"
    assert item["status"] == "planned"
    assert item["dispatch_allowed"] is False
    assert item["currency"] == "RUB"
    assert item["attribution"]["source"] == "telegram_ads"
    assert item["idempotency_key"].startswith("growth_conversion:v1:payment_success:")


def test_dry_run_conversion_rejects_unknown_type():
    with pytest.raises(ValueError, match="unsupported_conversion_type"):
        build_dry_run_conversion(
            conversion_type="budget_increase",
            source_platform="test",
            source_event="test",
            external_event_id="event_1",
        )


def test_provider_event_id_is_stable_identity_even_if_metadata_changes():
    first = build_dry_run_conversion(
        conversion_type="payment_success",
        source_platform="yookassa",
        source_event="payment.succeeded",
        external_event_id="pay_stable_1",
        user_id=101,
        amount_minor=190000,
        payload={"package_id": "p10", "provider_status": "succeeded"},
    )
    replay = build_dry_run_conversion(
        conversion_type="payment_success",
        source_platform="yookassa",
        source_event="payment.succeeded",
        external_event_id="pay_stable_1",
        user_id=101,
        amount_minor=190001,
        payload={"package_id": "p10", "provider_status": "succeeded", "extra": "corrected"},
    )

    assert first["idempotency_key"] == replay["idempotency_key"]


def test_conversion_outbox_is_idempotent_and_never_dispatchable(tmp_path, monkeypatch):
    path = tmp_path / "conversion_hub.db"
    _prepare(path)
    monkeypatch.setattr(growth_conversion_hub, "db", lambda: _fake_db(path))

    kwargs = {
        "conversion_type": "payment_success",
        "source_platform": "yookassa",
        "source_event": "payment.succeeded",
        "external_event_id": "pay_42",
        "user_id": 42,
        "amount_minor": 350000,
        "currency": "RUB",
        "attribution": {"source": "telegram_ads", "campaign": "launch", "creative": "reels1"},
        "payload": {"package_id": "practice_antistress_60"},
        "target_provider": "yandex_ads",
    }
    first = growth_conversion_hub.enqueue_conversion_dry_run(**kwargs)
    second = growth_conversion_hub.enqueue_conversion_dry_run(**kwargs)

    assert first.inserted is True
    assert second.inserted is False
    assert first.row_id == second.row_id
    assert first.idempotency_key == second.idempotency_key

    with _fake_db(path) as conn:
        row = conn.execute(
            "SELECT mode, status, dispatch_allowed, attempts, target_provider FROM growth_conversion_outbox"
        ).fetchone()
    assert row is not None
    assert row["mode"] == "dry_run"
    assert row["status"] == "planned"
    assert row["dispatch_allowed"] == 0
    assert row["attempts"] == 0
    assert row["target_provider"] == "yandex_ads"


def test_conversion_hub_snapshot_reports_dry_run_counts(tmp_path, monkeypatch):
    path = tmp_path / "conversion_hub_snapshot.db"
    _prepare(path)
    monkeypatch.setattr(growth_conversion_hub, "db", lambda: _fake_db(path))

    growth_conversion_hub.enqueue_conversion_dry_run(
        conversion_type="payment_success",
        source_platform="yookassa",
        source_event="payment.succeeded",
        external_event_id="pay_1",
        user_id=1,
        amount_minor=190000,
    )
    growth_conversion_hub.enqueue_conversion_dry_run(
        conversion_type="gift_paid",
        source_platform="yookassa",
        source_event="payment.succeeded",
        external_event_id="pay_2",
        user_id=2,
        amount_minor=350000,
    )

    snapshot = growth_conversion_hub.conversion_hub_snapshot("all")

    assert snapshot["mode"] == "dry_run"
    assert snapshot["dispatch_allowed"] is False
    assert snapshot["total"] == 2
    assert snapshot["counts"]["payment_success"] == 1
    assert snapshot["counts"]["gift_paid"] == 1


def test_payment_safe_ingestion_never_breaks_when_schema_is_missing(tmp_path, monkeypatch):
    path = tmp_path / "missing_schema.db"
    monkeypatch.setattr(growth_conversion_hub, "db", lambda: _fake_db(path))

    result = growth_conversion_hub.record_payment_conversion_dry_run_safe(
        source_platform="yookassa",
        source_event="payment.succeeded",
        external_event_id="pay_missing_schema",
        user_id=1,
        amount_minor=10000,
        currency="RUB",
    )

    assert result.inserted is False
    assert "schema_not_migrated" in result.error


def test_verified_yookassa_wrapper_enqueues_only_after_success(monkeypatch):
    from services.payments import verified_reconciliation

    captured = {}
    monkeypatch.setattr(verified_reconciliation, "verify_yookassa_webhook_with_provider", lambda payload: None)
    monkeypatch.setattr(
        verified_reconciliation,
        "record_yookassa_webhook",
        lambda payload: ReconciliationResult(
            ok=True,
            provider="yookassa",
            provider_payment_id="pay_verified_1",
            status="succeeded",
            event="payment.succeeded",
            inserted=True,
            processing_status="side_effects_done",
            side_effects_done=True,
        ),
    )
    monkeypatch.setattr(
        verified_reconciliation,
        "record_payment_conversion_dry_run_safe",
        lambda **kwargs: captured.update(kwargs),
    )
    payload = {
        "event": "payment.succeeded",
        "object": {
            "id": "pay_verified_1",
            "status": "succeeded",
            "amount": {"value": "1900.00", "currency": "RUB"},
            "metadata": {
                "external_user_id": "123",
                "kind": "tokens",
                "package_id": "p10",
                "source": "telegram_ads",
                "campaign": "may",
                "creative": "reels1",
            },
        },
    }

    result = verified_reconciliation.record_verified_yookassa_webhook(payload)

    assert result.ok is True
    assert captured["external_event_id"] == "pay_verified_1"
    assert captured["user_id"] == 123
    assert captured["amount_minor"] == 190000
    assert captured["currency"] == "RUB"
    assert captured["gift"] is False
    assert captured["attribution"]["source"] == "telegram_ads"


def test_verified_yookassa_wrapper_does_not_enqueue_problem_result(monkeypatch):
    from services.payments import verified_reconciliation

    called = False

    def _capture(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(verified_reconciliation, "verify_yookassa_webhook_with_provider", lambda payload: None)
    monkeypatch.setattr(
        verified_reconciliation,
        "record_yookassa_webhook",
        lambda payload: ReconciliationResult(
            ok=True,
            provider="yookassa",
            provider_payment_id="pay_problem",
            status="succeeded",
            event="payment.succeeded",
            inserted=True,
            problem="grant_failed",
            processing_status="action_required",
            side_effects_done=False,
        ),
    )
    monkeypatch.setattr(verified_reconciliation, "record_payment_conversion_dry_run_safe", _capture)

    verified_reconciliation.record_verified_yookassa_webhook(
        {
            "event": "payment.succeeded",
            "object": {
                "id": "pay_problem",
                "status": "succeeded",
                "amount": {"value": "100.00", "currency": "RUB"},
                "metadata": {"external_user_id": "123"},
            },
        }
    )

    assert called is False
