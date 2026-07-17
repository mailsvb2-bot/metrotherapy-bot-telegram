from __future__ import annotations

import sqlite3

from services.payments.reconciliation import ReconciliationResult
from services.payments import verified_reconciliation


class _DbCtx:
    def __init__(self, path):
        self.path = path
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        assert self.conn is not None
        self.conn.close()
        return False


def _result(*, ok: bool, problem: str = "", processing_status: str = "action_required"):
    return ReconciliationResult(
        ok=ok,
        provider="yookassa",
        provider_payment_id="payment-1",
        status="succeeded",
        event="payment.succeeded",
        inserted=False,
        problem=problem,
        processing_status=processing_status,
        side_effects_done=False,
    )


def test_retryable_entitlement_failure_returns_service_unavailable():
    status, retryable = verified_reconciliation.yookassa_webhook_http_status(
        _result(ok=True, problem="practice_grant_failed:RuntimeError")
    )

    assert status == 503
    assert retryable is True


def test_retryable_provider_network_failure_returns_service_unavailable():
    status, retryable = verified_reconciliation.yookassa_webhook_http_status(
        _result(
            ok=False,
            problem="provider_verification_failed:provider_network:TimeoutError",
        )
    )

    assert status == 503
    assert retryable is True


def test_permanent_reconciliation_problem_is_acknowledged_for_manual_review():
    status, retryable = verified_reconciliation.yookassa_webhook_http_status(
        _result(ok=True, problem="amount_mismatch_for_practice_grant")
    )

    assert status == 200
    assert retryable is False


def test_invalid_provider_fact_remains_bad_request():
    status, retryable = verified_reconciliation.yookassa_webhook_http_status(
        _result(
            ok=False,
            problem="provider_verification_failed:provider_amount_mismatch",
        )
    )

    assert status == 400
    assert retryable is False


def test_late_payment_event_preserves_completed_refund(tmp_path, monkeypatch):
    db_path = tmp_path / "payments.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE payments(
                provider_charge_id TEXT,
                telegram_charge_id TEXT,
                provider_status TEXT,
                processing_status TEXT,
                problem TEXT,
                side_effects_done_at_utc TEXT
            )
            """.strip()
        )
        conn.execute(
            """
            INSERT INTO payments(
                provider_charge_id, telegram_charge_id, provider_status,
                processing_status, problem, side_effects_done_at_utc
            ) VALUES(?,?,?,?,?,?)
            """.strip(),
            (
                "payment-refunded",
                "yookassa:payment-refunded",
                "refunded",
                "refunded",
                "",
                "2026-07-17T00:00:00+00:00",
            ),
        )

    monkeypatch.setattr(verified_reconciliation, "db", lambda: _DbCtx(db_path))
    monkeypatch.setattr(
        verified_reconciliation,
        "verify_yookassa_webhook_with_provider",
        lambda _payload: {
            "id": "payment-refunded",
            "status": "succeeded",
            "amount": {"value": "1900.00", "currency": "RUB"},
            "metadata": {
                "external_user_id": "123",
                "kind": "tokens",
                "package_id": "practice_60",
            },
        },
    )

    def fail_if_reconciled(_payload):
        raise AssertionError("late payment event must not overwrite refund state")

    monkeypatch.setattr(verified_reconciliation, "record_yookassa_webhook", fail_if_reconciled)

    result = verified_reconciliation.record_verified_yookassa_webhook(
        {
            "event": "payment.succeeded",
            "object": {"id": "payment-refunded"},
        }
    )

    assert result.ok is True
    assert result.inserted is False
    assert result.status == "refunded"
    assert result.processing_status == "refunded"
    assert result.side_effects_done is True


def test_late_payment_event_preserves_partial_refund_review(tmp_path, monkeypatch):
    db_path = tmp_path / "payments.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE payments(
                provider_charge_id TEXT,
                telegram_charge_id TEXT,
                provider_status TEXT,
                processing_status TEXT,
                problem TEXT,
                side_effects_done_at_utc TEXT
            )
            """.strip()
        )
        conn.execute(
            """
            INSERT INTO payments(
                provider_charge_id, telegram_charge_id, provider_status,
                processing_status, problem, side_effects_done_at_utc
            ) VALUES(?,?,?,?,?,?)
            """.strip(),
            (
                "payment-partial",
                "yookassa:payment-partial",
                "succeeded",
                "refund_partial_recorded",
                "partial_refund_requires_manual_policy",
                None,
            ),
        )

    monkeypatch.setattr(verified_reconciliation, "db", lambda: _DbCtx(db_path))

    result = verified_reconciliation._preserved_refund_result(
        {
            "event": "payment.succeeded",
            "object": {"id": "payment-partial", "status": "succeeded"},
        }
    )

    assert result is not None
    assert result.status == "succeeded"
    assert result.processing_status == "refund_partial_recorded"
    assert result.problem == "partial_refund_requires_manual_policy"
    assert result.side_effects_done is False
