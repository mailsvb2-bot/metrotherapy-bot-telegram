from __future__ import annotations

from services.db import db
from services.payments import reconciliation, retry_queue, verified_reconciliation
from services.payments.reconciliation import ReconciliationResult
from services.payments.yookassa_provider import YooKassaProviderVerificationError


def _payload(payment_id: str) -> dict:
    return {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": "2499.00", "currency": "RUB"},
            "metadata": {
                "project": "metrotherapy",
                "user_id": "885001",
                "external_user_id": "885001",
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


def _delete_retry(payment_id: str) -> None:
    with db() as conn:
        conn.execute(
            "DELETE FROM payment_reconciliation_retry WHERE provider=? AND provider_payment_id=?",
            ("yookassa", payment_id),
        )


def _retry_row(payment_id: str):
    with db() as conn:
        return conn.execute(
            """
            SELECT status,attempts,last_error,lock_token,completed_at,payload_json
            FROM payment_reconciliation_retry
            WHERE provider=? AND provider_payment_id=?
            LIMIT 1
            """.strip(),
            ("yookassa", payment_id),
        ).fetchone()


def test_verified_local_failure_is_durable_and_worker_completes_it(monkeypatch) -> None:
    payment_id = "yk-durable-retry-885001"
    payload = _payload(payment_id)
    _delete_retry(payment_id)

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
    assert row["status"] in {"pending", "retry"}
    assert int(row["attempts"]) == 0
    assert payment_id in row["payload_json"]

    monkeypatch.setattr(reconciliation, "record_yookassa_webhook", lambda _payload: _success(payment_id))
    batch = retry_queue.run_payment_retry_batch(limit=10)

    assert batch.claimed >= 1
    assert batch.completed >= 1
    row = _retry_row(payment_id)
    assert row["status"] == "completed"
    assert row["completed_at"]
    assert row["lock_token"] is None
    assert row["last_error"] == ""
    _delete_retry(payment_id)


def test_unverified_payload_is_never_enqueued(monkeypatch) -> None:
    payment_id = "yk-unverified-no-retry-885002"
    payload = _payload(payment_id)
    _delete_retry(payment_id)

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
    payment_id = "yk-retry-dead-885003"
    payload = _payload(payment_id)
    _delete_retry(payment_id)
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
    _delete_retry(payment_id)


def test_successful_provider_replay_closes_existing_retry(monkeypatch) -> None:
    payment_id = "yk-provider-replay-closes-885004"
    payload = _payload(payment_id)
    _delete_retry(payment_id)
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
    _delete_retry(payment_id)


def test_claim_lease_is_exclusive() -> None:
    payment_id = "yk-exclusive-lease-885005"
    payload = _payload(payment_id)
    _delete_retry(payment_id)
    retry_queue.enqueue_verified_payment_retry(payload, _transient(payment_id))

    first = retry_queue.claim_due_payment_retries(limit=1)
    second = retry_queue.claim_due_payment_retries(limit=1)

    assert len(first) == 1
    assert second == []
    assert first[0].provider_payment_id == payment_id
    assert first[0].lock_token
    _delete_retry(payment_id)
