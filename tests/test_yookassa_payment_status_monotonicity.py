from __future__ import annotations

from services.db import db
from services.payments.reconciliation import record_yookassa_webhook
from services.payments.yookassa_refunds import record_yookassa_refund
from services.practice_tokens import get_wallet, reserve_practice


def _payment(*, payment_id: str, user_id: int) -> dict:
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


def _refund(*, refund_id: str, payment_id: str, amount: str) -> dict:
    return {
        "event": "refund.succeeded",
        "object": {
            "id": refund_id,
            "status": "succeeded",
            "payment_id": payment_id,
            "amount": {"value": amount, "currency": "RUB"},
        },
    }


def _payment_row(payment_id: str):
    with db() as conn:
        return conn.execute(
            """
            SELECT provider_status, processing_status, problem, processing_error
            FROM payments
            WHERE provider_charge_id=? OR telegram_charge_id=?
            LIMIT 1
            """.strip(),
            (payment_id, f"yookassa:{payment_id}"),
        ).fetchone()


def test_late_succeeded_event_cannot_reopen_completed_refund() -> None:
    user_id = 884001
    payment_id = "yk-monotonic-refunded-884001"
    payment = _payment(payment_id=payment_id, user_id=user_id)

    assert record_yookassa_webhook(payment).side_effects_done is True
    completed = record_yookassa_refund(
        _refund(refund_id="refund-monotonic-full-884001", payment_id=payment_id, amount="2499.00")
    )
    assert completed.processing_status == "refunded"
    assert get_wallet(user_id).available_tokens == 0

    late = record_yookassa_webhook(payment)

    assert late.ok is True
    assert late.inserted is False
    assert late.status == "refunded"
    assert late.processing_status == "refunded"
    assert late.side_effects_done is True
    assert late.problem == ""
    assert get_wallet(user_id).available_tokens == 0

    row = _payment_row(payment_id)
    assert row["provider_status"] == "refunded"
    assert row["processing_status"] == "refunded"
    assert row["problem"] == ""
    assert row["processing_error"] == ""
    with db() as conn:
        grants = conn.execute(
            "SELECT COUNT(*) AS n FROM payment_token_grants WHERE provider='yookassa' AND provider_payment_id=?",
            (payment_id,),
        ).fetchone()
    assert int(grants["n"]) == 1


def test_late_succeeded_event_preserves_partial_refund_review_state() -> None:
    user_id = 884002
    payment_id = "yk-monotonic-partial-884002"
    payment = _payment(payment_id=payment_id, user_id=user_id)

    record_yookassa_webhook(payment)
    partial = record_yookassa_refund(
        _refund(refund_id="refund-monotonic-partial-884002", payment_id=payment_id, amount="1000.00")
    )
    assert partial.processing_status == "refund_partial_recorded"

    late = record_yookassa_webhook(payment)

    assert late.status == "succeeded"
    assert late.processing_status == "refund_partial_recorded"
    assert late.problem == "partial_refund_requires_manual_policy"
    assert late.side_effects_done is False
    assert get_wallet(user_id).available_tokens == 7

    row = _payment_row(payment_id)
    assert row["provider_status"] == "succeeded"
    assert row["processing_status"] == "refund_partial_recorded"
    assert row["problem"] == "partial_refund_requires_manual_policy"


def test_late_succeeded_event_preserves_refund_action_required_state() -> None:
    user_id = 884003
    payment_id = "yk-monotonic-action-required-884003"
    payment = _payment(payment_id=payment_id, user_id=user_id)

    record_yookassa_webhook(payment)
    reserved, _wallet, reservation_id = reserve_practice(user_id, audio_anchor=884003)
    assert reserved is True
    assert reservation_id
    action_required = record_yookassa_refund(
        _refund(refund_id="refund-monotonic-action-884003", payment_id=payment_id, amount="2499.00")
    )
    assert action_required.processing_status == "refund_action_required"

    late = record_yookassa_webhook(payment)

    assert late.status == "succeeded"
    assert late.processing_status == "refund_action_required"
    assert late.problem == "purchased_practices_already_used_or_reserved"
    assert late.side_effects_done is False
    wallet = get_wallet(user_id)
    assert wallet.available_tokens == 6
    assert wallet.reserved_tokens == 1

    row = _payment_row(payment_id)
    assert row["provider_status"] == "succeeded"
    assert row["processing_status"] == "refund_action_required"
    assert row["problem"] == "purchased_practices_already_used_or_reserved"
