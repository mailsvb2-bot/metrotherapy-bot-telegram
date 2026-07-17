from __future__ import annotations

from services.db import db
from services.gift_claims import claim_gift_token, create_gift_checkout_token
from services.payments.reconciliation import record_yookassa_webhook
from services.payments.yookassa_provider import verify_yookassa_refund_webhook_with_provider
from services.payments.yookassa_refunds import record_yookassa_refund
from services.practice_tokens import get_wallet, reserve_practice


def _payment(*, payment_id: str, user_id: int, package_id: str, amount: str, gift_token: str = "") -> dict:
    metadata = {
        "project": "metrotherapy",
        "user_id": str(user_id),
        "external_user_id": str(user_id),
        "source": "max",
        "kind": "tokens",
        "package_id": package_id,
    }
    if gift_token:
        metadata["gift_token"] = gift_token
    return {
        "event": "payment.succeeded",
        "object": {
            "id": payment_id,
            "status": "succeeded",
            "amount": {"value": amount, "currency": "RUB"},
            "metadata": metadata,
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


def _row(sql: str, params: tuple = ()):
    with db() as conn:
        return conn.execute(sql, params).fetchone()


def test_full_refund_revokes_exact_unused_payment_lot_once() -> None:
    user_id = 883001
    payment_id = "yk-refund-full-883001"
    payload = _payment(
        payment_id=payment_id,
        user_id=user_id,
        package_id="practice_start_7",
        amount="2499.00",
    )
    assert record_yookassa_webhook(payload).side_effects_done is True
    assert get_wallet(user_id).available_tokens == 7

    refund = _refund(refund_id="refund-full-883001", payment_id=payment_id, amount="2499.00")
    first = record_yookassa_refund(refund)
    second = record_yookassa_refund(refund)

    assert first.ok is True
    assert first.processing_status == "refunded"
    assert first.side_effects_done is True
    assert second.ok is True
    assert second.processing_status == "refunded"
    assert get_wallet(user_id).available_tokens == 0

    lot = _row(
        "SELECT available_tokens, refunded_tokens FROM practice_token_lots WHERE provider='yookassa' AND provider_payment_id=?",
        (payment_id,),
    )
    assert int(lot["available_tokens"]) == 0
    assert int(lot["refunded_tokens"]) == 7
    ledger = _row(
        "SELECT COUNT(*) AS n FROM practice_ledger WHERE idempotency_key=?",
        (f"yookassa_refund_finalize:{payment_id}",),
    )
    assert int(ledger["n"]) == 1


def test_partial_refunds_accumulate_without_proportional_token_guessing() -> None:
    user_id = 883002
    payment_id = "yk-refund-partial-883002"
    record_yookassa_webhook(
        _payment(
            payment_id=payment_id,
            user_id=user_id,
            package_id="practice_start_7",
            amount="2499.00",
        )
    )

    partial = record_yookassa_refund(
        _refund(refund_id="refund-partial-a-883002", payment_id=payment_id, amount="1000.00")
    )
    assert partial.ok is True
    assert partial.processing_status == "refund_partial_recorded"
    assert partial.problem == "partial_refund_requires_manual_policy"
    assert get_wallet(user_id).available_tokens == 7

    completed = record_yookassa_refund(
        _refund(refund_id="refund-partial-b-883002", payment_id=payment_id, amount="1499.00")
    )
    assert completed.processing_status == "refunded"
    assert completed.side_effects_done is True
    assert get_wallet(user_id).available_tokens == 0


def test_used_or_reserved_exact_lot_becomes_action_required() -> None:
    user_id = 883003
    payment_id = "yk-refund-used-883003"
    record_yookassa_webhook(
        _payment(
            payment_id=payment_id,
            user_id=user_id,
            package_id="practice_start_7",
            amount="2499.00",
        )
    )
    reserved, _wallet, reservation_id = reserve_practice(user_id, audio_anchor=883003)
    assert reserved is True
    assert reservation_id

    result = record_yookassa_refund(
        _refund(refund_id="refund-used-883003", payment_id=payment_id, amount="2499.00")
    )
    assert result.ok is True
    assert result.processing_status == "refund_action_required"
    assert result.problem == "purchased_practices_already_used_or_reserved"
    wallet = get_wallet(user_id)
    assert wallet.available_tokens == 6
    assert wallet.reserved_tokens == 1
    state = _row(
        "SELECT status, debt_tokens FROM yookassa_refunds WHERE refund_id=?",
        ("refund-used-883003",),
    )
    assert state["status"] == "action_required"
    assert int(state["debt_tokens"]) == 1


def test_unclaimed_gift_is_cancelled_but_claimed_gift_requires_review() -> None:
    buyer_id = 883004
    unclaimed_payment = "yk-refund-gift-unclaimed-883004"
    unclaimed_token = create_gift_checkout_token(
        buyer_user_id=buyer_id,
        package_id="practice_start_7",
        source_platform="max",
    )
    record_yookassa_webhook(
        _payment(
            payment_id=unclaimed_payment,
            user_id=buyer_id,
            package_id="practice_start_7",
            amount="2499.00",
            gift_token=unclaimed_token,
        )
    )
    unclaimed = record_yookassa_refund(
        _refund(
            refund_id="refund-gift-unclaimed-883004",
            payment_id=unclaimed_payment,
            amount="2499.00",
        )
    )
    assert unclaimed.processing_status == "refunded"
    gift = _row("SELECT status FROM gift_claims WHERE gift_token=?", (unclaimed_token,))
    assert gift["status"] == "refunded"
    assert get_wallet(buyer_id).available_tokens == 0

    claimed_payment = "yk-refund-gift-claimed-883004"
    claimed_token = create_gift_checkout_token(
        buyer_user_id=buyer_id,
        package_id="practice_start_7",
        source_platform="max",
    )
    record_yookassa_webhook(
        _payment(
            payment_id=claimed_payment,
            user_id=buyer_id,
            package_id="practice_start_7",
            amount="2499.00",
            gift_token=claimed_token,
        )
    )
    recipient_id = 883005
    assert claim_gift_token(
        gift_token=claimed_token,
        recipient_user_id=recipient_id,
        platform="max",
    ).ok
    claimed = record_yookassa_refund(
        _refund(
            refund_id="refund-gift-claimed-883004",
            payment_id=claimed_payment,
            amount="2499.00",
        )
    )
    assert claimed.processing_status == "refund_action_required"
    assert claimed.problem == "gift_already_claimed"
    assert get_wallet(recipient_id).available_tokens == 7


def test_pending_premium_is_revoked_but_delivered_premium_requires_review() -> None:
    user_id = 883006
    payment_id = "yk-refund-premium-pending-883006"
    record_yookassa_webhook(
        _payment(
            payment_id=payment_id,
            user_id=user_id,
            package_id="practice_antistress_60",
            amount="8290.00",
        )
    )
    completed = record_yookassa_refund(
        _refund(refund_id="refund-premium-pending-883006", payment_id=payment_id, amount="8290.00")
    )
    assert completed.processing_status == "refunded"
    entitlement = _row(
        "SELECT DISTINCT status FROM premium_entitlements WHERE provider='yookassa' AND provider_payment_id=?",
        (payment_id,),
    )
    assert entitlement["status"] == "revoked"
    outbox = _row(
        "SELECT DISTINCT status FROM premium_delivery_outbox WHERE idempotency_key LIKE ?",
        (f"premium_delivery:yookassa:{payment_id}:%",),
    )
    assert outbox["status"] == "cancelled"

    delivered_user = 883007
    delivered_payment = "yk-refund-premium-sent-883007"
    record_yookassa_webhook(
        _payment(
            payment_id=delivered_payment,
            user_id=delivered_user,
            package_id="practice_antistress_60",
            amount="8290.00",
        )
    )
    with db() as conn:
        conn.execute(
            "UPDATE premium_delivery_outbox SET status='sent' WHERE idempotency_key LIKE ?",
            (f"premium_delivery:yookassa:{delivered_payment}:%",),
        )
        conn.commit()
    action_required = record_yookassa_refund(
        _refund(refund_id="refund-premium-sent-883007", payment_id=delivered_payment, amount="8290.00")
    )
    assert action_required.processing_status == "refund_action_required"
    assert action_required.problem == "premium_content_already_delivered"
    assert get_wallet(delivered_user).available_tokens == 60


def test_refund_provider_verification_checks_payment_amount_and_currency(monkeypatch) -> None:
    monkeypatch.setenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED", "1")
    payload = _refund(refund_id="refund-verify-883008", payment_id="payment-verify-883008", amount="10.00")
    provider = dict(payload["object"])
    monkeypatch.setattr(
        "services.payments.yookassa_provider.fetch_yookassa_refund",
        lambda refund_id: dict(provider),
    )
    assert verify_yookassa_refund_webhook_with_provider(payload) == provider

    mismatched = dict(provider)
    mismatched["payment_id"] = "other-payment"
    monkeypatch.setattr(
        "services.payments.yookassa_provider.fetch_yookassa_refund",
        lambda refund_id: dict(mismatched),
    )
    from services.payments.yookassa_provider import YooKassaProviderVerificationError

    try:
        verify_yookassa_refund_webhook_with_provider(payload)
    except YooKassaProviderVerificationError as exc:
        assert "payment_id_mismatch" in str(exc)
    else:
        raise AssertionError("mismatched refund payment_id must be rejected")
