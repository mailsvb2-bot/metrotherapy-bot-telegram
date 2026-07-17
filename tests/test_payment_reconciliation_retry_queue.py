from __future__ import annotations

from services.db import db
from services.payments import reconciliation, retry_queue, verified_reconciliation
from services.payments.reconciliation import ReconciliationResult
from services.payments.yookassa_provider import YooKassaProviderVerificationError
from services.privacy_controls import export_user_data_snapshot
from services.privacy_manifest import POLICIES


def _payload(payment_id: str, *, user_id: int = 885001) -> dict:
    return {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": "2499.00", "currency": "RUB"},
            "metadata": {
                "project": "metrotherapy",
                "user_id": str(user_id),
                "external_user_id": str(user_id),
                "source": "max",
                "kind": "tokens",
                "package_id": "practice_start_7",
            },
        },
    }


def _transient(payment_id: str, problem: str = "practice_grant_failed:RuntimeError") -> ReconciliationResult:
    return ReconciliationResult(
        ok=True,
        provider="yookassa",
        provider_payment_id=payment_id,
        status="succeeded",
        event="payment.succeeded",
        inserted=False,
        problem=problem,
        processing_status="action_required",
        side_effects_done=False,
    )


def _success(payment_id: str) -> ReconciliationResult:
    return ReconciliationResult(
        ok=True,
        provider="yookassa",
        provider_payment_id=payment_id,
        status="succeeded",
        event="payment.succeeded",
        inserted=False,
        problem="",
        processing_status="side_effects_done",
        side_effects_done=True,
    )


def _permanent_failure(payment_id: str) -> ReconciliationResult:
    return ReconciliationResult(
        ok=False,
        provider="yookassa",
        provider_payment_id=payment_id,
        status="succeeded",
        event="payment.succeeded",
        inserted=False,
        problem="missing_user_id",
        processing_status="action_required",
        side_effects_done=False,
    )


def _clear_retries() -> None:
    with db() as conn:
        conn.execute("DELETE FROM payment_reconciliation_retry")


def _retry_row(payment_id: str):
    with db() as conn:
        return conn.execute(
            """
            SELECT user_id,status,attempts,last_error,lock_token,completed_at,payload_json
            FROM payment_reconciliation_retry
            WHERE provider=? AND provider_payment_id=?
            LIMIT 1
            """.strip(),
            ("yookassa", payment_id),
        ).fetchone()


def test_verified_local_failure_is_durable_and_worker_completes_it(monkeypatch) -> None:
    _clear_retries()
    payment_id = "yk-durable-retry-885001"
    payload = _payload(payment_id)

    monkeypatch.setattr(
        verified_reconciliation,
        "verify_yookassa_webhook_with_provider",
        lambda _payload: dict(payload["object"]),
    )
    monkeypatch.setattr(
        verified_reconciliation,
        "record_yookassa_webhook",
        lambda _payload: _transient(payment_id),
    )

    result = verified_reconciliation.record_verified_yookassa_webhook(payload)

    assert result.problem.startswith("practice_grant_failed:")
    row = _retry_row(payment_id)
    assert row is not None
    assert int(row["user_id"]) == 885001
    assert row["status"] in {"pending", "retry"}
    assert int(row["attempts"]) == 0
    assert payment_id in row["payload_json"]

    monkeypatch.setattr(reconciliation, "record_yookassa_webhook", lambda _payload: _success(payment_id))
    batch = retry_queue.run_payment_retry_batch(limit=10)

    assert batch.claimed == 1
    assert batch.completed == 1
    row = _retry_row(payment_id)
    assert row["status"] == "completed"
    assert row["completed_at"]
    assert row["lock_token"] is None
    assert row["last_error"] == ""
    _clear_retries()


def test_unverified_payload_is_never_enqueued(monkeypatch) -> None:
    _clear_retries()
    payment_id = "yk-unverified-no-retry-885002"
    payload = _payload(payment_id)

    def fail_verification(_payload):
        raise YooKassaProviderVerificationError("provider_network:synthetic")

    monkeypatch.setattr(
        verified_reconciliation,
        "verify_yookassa_webhook_with_provider",
        fail_verification,
    )

    result = verified_reconciliation.record_verified_yookassa_webhook(payload)

    assert result.ok is False
    assert result.problem.startswith("provider_verification_failed:")
    assert _retry_row(payment_id) is None


def test_transient_retry_becomes_dead_after_bounded_attempts(monkeypatch) -> None:
    _clear_retries()
    payment_id = "yk-retry-dead-885003"
    payload = _payload(payment_id)
    retry_queue.enqueue_verified_payment_retry(payload, _transient(payment_id))
    monkeypatch.setenv("PAYMENT_RETRY_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("PAYMENT_RETRY_BASE_DELAY_SEC", "1")
    monkeypatch.setenv("PAYMENT_RETRY_MAX_DELAY_SEC", "30")
    monkeypatch.setattr(
        reconciliation,
        "record_yookassa_webhook",
        lambda _payload: _transient(payment_id, "gift_mark_failed:RuntimeError"),
    )

    first = retry_queue.run_payment_retry_batch(limit=1)
    assert first.rescheduled == 1
    row = _retry_row(payment_id)
    assert row["status"] == "retry"
    assert int(row["attempts"]) == 1

    with db() as conn:
        conn.execute(
            "UPDATE payment_reconciliation_retry SET available_at=? WHERE provider=? AND provider_payment_id=?",
            ("2000-01-01T00:00:00+00:00", "yookassa", payment_id),
        )

    second = retry_queue.run_payment_retry_batch(limit=1)
    assert second.dead == 1
    row = _retry_row(payment_id)
    assert row["status"] == "dead"
    assert int(row["attempts"]) == 2
    assert "gift_mark_failed" in row["last_error"]
    assert row["lock_token"] is None
    _clear_retries()


def test_non_retryable_worker_result_becomes_visible_dead_letter(monkeypatch) -> None:
    _clear_retries()
    payment_id = "yk-retry-permanent-885004"
    payload = _payload(payment_id)
    retry_queue.enqueue_verified_payment_retry(payload, _transient(payment_id))
    monkeypatch.setattr(
        reconciliation,
        "record_yookassa_webhook",
        lambda _payload: _permanent_failure(payment_id),
    )

    batch = retry_queue.run_payment_retry_batch(limit=1)

    assert batch.claimed == 1
    assert batch.dead == 1
    row = _retry_row(payment_id)
    assert row["status"] == "dead"
    assert int(row["attempts"]) == 1
    assert row["last_error"] == "missing_user_id"
    _clear_retries()


def test_successful_provider_replay_closes_existing_retry(monkeypatch) -> None:
    _clear_retries()
    payment_id = "yk-provider-replay-closes-885005"
    payload = _payload(payment_id)
    retry_queue.enqueue_verified_payment_retry(payload, _transient(payment_id))

    monkeypatch.setattr(
        verified_reconciliation,
        "verify_yookassa_webhook_with_provider",
        lambda _payload: dict(payload["object"]),
    )
    monkeypatch.setattr(
        verified_reconciliation,
        "record_yookassa_webhook",
        lambda _payload: _success(payment_id),
    )

    result = verified_reconciliation.record_verified_yookassa_webhook(payload)

    assert result.side_effects_done is True
    row = _retry_row(payment_id)
    assert row["status"] == "completed"
    assert row["completed_at"]
    _clear_retries()


def test_successful_provider_replay_recovers_previous_dead_letter(monkeypatch) -> None:
    _clear_retries()
    payment_id = "yk-provider-replay-recovers-dead-885006"
    payload = _payload(payment_id)
    retry_queue.enqueue_verified_payment_retry(payload, _transient(payment_id))
    with db() as conn:
        conn.execute(
            "UPDATE payment_reconciliation_retry SET status='dead',attempts=48,last_error=? "
            "WHERE provider=? AND provider_payment_id=?",
            ("synthetic_dead", "yookassa", payment_id),
        )

    monkeypatch.setattr(
        verified_reconciliation,
        "verify_yookassa_webhook_with_provider",
        lambda _payload: dict(payload["object"]),
    )
    monkeypatch.setattr(
        verified_reconciliation,
        "record_yookassa_webhook",
        lambda _payload: _success(payment_id),
    )

    result = verified_reconciliation.record_verified_yookassa_webhook(payload)

    assert result.side_effects_done is True
    row = _retry_row(payment_id)
    assert row["status"] == "completed"
    assert row["completed_at"]
    assert row["last_error"] == ""
    _clear_retries()


def test_claim_lease_is_exclusive() -> None:
    _clear_retries()
    payment_id = "yk-exclusive-lease-885007"
    payload = _payload(payment_id)
    retry_queue.enqueue_verified_payment_retry(payload, _transient(payment_id))

    first = retry_queue.claim_due_payment_retries(limit=1)
    second = retry_queue.claim_due_payment_retries(limit=1)

    assert len(first) == 1
    assert second == []
    assert first[0].provider_payment_id == payment_id
    assert first[0].lock_token
    _clear_retries()


def test_retry_rows_are_declared_retained_privacy_facts() -> None:
    _clear_retries()
    user_id = 885008
    payment_id = "yk-privacy-owned-885008"
    payload = _payload(payment_id, user_id=user_id)
    retry_queue.enqueue_verified_payment_retry(payload, _transient(payment_id))

    policy = POLICIES["payment_reconciliation_retry"]
    assert policy.disposition == "retain"
    assert policy.required is True
    assert policy.ownership_columns == ("user_id",)

    snapshot = export_user_data_snapshot(user_id)
    rows = snapshot["tables"]["payment_reconciliation_retry"]
    assert len(rows) == 1
    assert int(rows[0]["user_id"]) == user_id
    assert rows[0]["provider_payment_id"] == payment_id
    _clear_retries()
