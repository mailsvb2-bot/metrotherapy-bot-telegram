from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from handlers import payments as payment_handler
from services.db import db
from services.gift_claims import claim_gift_token, create_gift_checkout_token
from services.payments import telegram_stars
from services.payments.telegram_stars import build_stars_payload, record_successful_stars_payment
from services.payments.telegram_stars_refunds import (
    cancel_prepared_stars_refund,
    complete_stars_refund,
    mark_stars_refund_provider_succeeded,
    prepare_stars_refund,
    preview_stars_refund,
)
from services.practice_token_contract import telegram_stars_price
from services.practice_tokens import get_wallet, reserve_practice


def _charge(label: str) -> str:
    return f"stars-refund-{label}-{uuid.uuid4().hex}"


def _pay(monkeypatch, *, user_id: int, package_id: str, charge_id: str, gift_token: str = "") -> None:
    monkeypatch.setattr(telegram_stars, "log_event", lambda *args, **kwargs: None)
    payload = build_stars_payload(
        buyer_user_id=user_id,
        package_id=package_id,
        gift_token=gift_token,
    )
    record_successful_stars_payment(
        user_id=user_id,
        payload=payload,
        total_amount=telegram_stars_price(package_id),
        currency="XTR",
        telegram_charge_id=charge_id,
    )


def test_stars_refund_hold_can_be_released_and_retried(monkeypatch) -> None:
    user_id = 782001
    charge_id = _charge("retry")
    _pay(monkeypatch, user_id=user_id, package_id="practice_start_7", charge_id=charge_id)

    plan = preview_stars_refund(charge_id)
    assert plan.refundable is True
    assert plan.tokens == 7

    prepared = prepare_stars_refund(charge_id, requested_by=900001)
    assert prepared.status == "prepared"
    assert get_wallet(user_id).available_tokens == 0

    cancelled = cancel_prepared_stars_refund(charge_id, error="provider unavailable")
    assert cancelled.refundable is True
    assert get_wallet(user_id).available_tokens == 7

    prepared_again = prepare_stars_refund(charge_id, requested_by=900001)
    assert prepared_again.status == "prepared"
    assert prepared_again.attempt == 2
    mark_stars_refund_provider_succeeded(charge_id)
    completed = complete_stars_refund(charge_id)
    duplicate = complete_stars_refund(charge_id)

    assert completed.status == "completed"
    assert duplicate.status == "completed"
    wallet = get_wallet(user_id)
    assert wallet.available_tokens == 0
    with db() as conn:
        wallet_row = conn.execute(
            "SELECT refunded_tokens FROM practice_wallets WHERE user_id=?",
            (user_id,),
        ).fetchone()
        payment = conn.execute(
            "SELECT provider_status, processing_status FROM payments WHERE telegram_charge_id=?",
            (charge_id,),
        ).fetchone()
        finalized = conn.execute(
            "SELECT COUNT(*) AS n FROM practice_ledger WHERE idempotency_key LIKE ?",
            (f"stars_refund_finalize:{charge_id}:%",),
        ).fetchone()
    assert int(wallet_row["refunded_tokens"]) == 7
    assert payment["provider_status"] == "refunded"
    assert payment["processing_status"] == "refunded"
    assert int(finalized["n"]) == 1


def test_stars_refund_is_blocked_after_any_purchased_practice_is_reserved(monkeypatch) -> None:
    user_id = 782011
    charge_id = _charge("used")
    _pay(monkeypatch, user_id=user_id, package_id="practice_start_7", charge_id=charge_id)
    reserved, _wallet, reservation_id = reserve_practice(user_id, audio_anchor=991001)
    assert reserved is True
    assert reservation_id

    plan = preview_stars_refund(charge_id)

    assert plan.refundable is False
    assert plan.reason == "purchased_practices_already_used_or_reserved"


def test_charged_payment_without_entitlement_can_still_be_refunded(monkeypatch) -> None:
    monkeypatch.setattr(telegram_stars, "log_event", lambda *args, **kwargs: None)
    user_id = 782016
    charge_id = _charge("manual-recovery")
    with pytest.raises(telegram_stars.StarsPaymentError):
        record_successful_stars_payment(
            user_id=user_id,
            payload="invalid-after-charge",
            total_amount=1900,
            currency="XTR",
            telegram_charge_id=charge_id,
        )

    plan = preview_stars_refund(charge_id)
    assert plan.refundable is True
    assert plan.reason == "charged_without_entitlement"
    assert plan.tokens == 0

    prepare_stars_refund(charge_id, requested_by=900001)
    mark_stars_refund_provider_succeeded(charge_id)
    assert complete_stars_refund(charge_id).status == "completed"


def test_unclaimed_stars_gift_can_be_refunded(monkeypatch) -> None:
    buyer_id = 782021
    charge_id = _charge("gift")
    gift_token = create_gift_checkout_token(
        buyer_user_id=buyer_id,
        package_id="practice_start_7",
        source_platform="telegram",
    )
    _pay(
        monkeypatch,
        user_id=buyer_id,
        package_id="practice_start_7",
        charge_id=charge_id,
        gift_token=gift_token,
    )

    assert preview_stars_refund(charge_id).reason == "gift_unclaimed"
    prepare_stars_refund(charge_id, requested_by=900001)
    mark_stars_refund_provider_succeeded(charge_id)
    complete_stars_refund(charge_id)

    with db() as conn:
        gift = conn.execute(
            "SELECT status FROM gift_claims WHERE gift_token=?",
            (gift_token,),
        ).fetchone()
    assert gift["status"] == "refunded"


def test_claimed_stars_gift_requires_manual_review(monkeypatch) -> None:
    buyer_id = 782031
    recipient_id = 782032
    charge_id = _charge("claimed-gift")
    gift_token = create_gift_checkout_token(
        buyer_user_id=buyer_id,
        package_id="practice_start_7",
        source_platform="telegram",
    )
    _pay(
        monkeypatch,
        user_id=buyer_id,
        package_id="practice_start_7",
        charge_id=charge_id,
        gift_token=gift_token,
    )
    assert claim_gift_token(
        gift_token=gift_token,
        recipient_user_id=recipient_id,
        platform="telegram",
    ).ok

    plan = preview_stars_refund(charge_id)

    assert plan.refundable is False
    assert plan.reason == "gift_already_claimed"


def test_pending_premium_side_effects_are_cancelled_on_refund(monkeypatch) -> None:
    user_id = 782041
    charge_id = _charge("premium")
    _pay(monkeypatch, user_id=user_id, package_id="practice_antistress_60", charge_id=charge_id)

    assert preview_stars_refund(charge_id).refundable is True
    prepare_stars_refund(charge_id, requested_by=900001)
    mark_stars_refund_provider_succeeded(charge_id)
    complete_stars_refund(charge_id)

    with db() as conn:
        entitlements = conn.execute(
            "SELECT DISTINCT status FROM premium_entitlements WHERE provider='telegram_stars' AND provider_payment_id=?",
            (charge_id,),
        ).fetchall()
        outbox = conn.execute(
            "SELECT DISTINCT status FROM premium_delivery_outbox WHERE idempotency_key LIKE ?",
            (f"premium_delivery:telegram_stars:{charge_id}:%",),
        ).fetchall()
    assert {row["status"] for row in entitlements} == {"revoked"}
    assert {row["status"] for row in outbox} == {"cancelled"}


@pytest.mark.asyncio
async def test_admin_refund_command_requires_preview_then_confirmation(monkeypatch) -> None:
    user_id = 782051
    charge_id = _charge("handler")
    _pay(monkeypatch, user_id=user_id, package_id="practice_start_7", charge_id=charge_id)
    monkeypatch.setattr(payment_handler, "is_platform_admin", lambda _user_id: True)
    monkeypatch.setattr(payment_handler, "log_event", lambda *args, **kwargs: None)

    answers: list[str] = []
    provider_calls: list[tuple[int, str]] = []

    class FakeBot:
        async def refund_star_payment(self, *, user_id: int, telegram_payment_charge_id: str) -> bool:
            provider_calls.append((user_id, telegram_payment_charge_id))
            return True

    async def answer(text: str, **_kwargs) -> None:
        answers.append(text)

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=900001),
        text=f"/refundstars {charge_id}",
        answer=answer,
        bot=FakeBot(),
    )
    await payment_handler.cmd_refund_stars(message)  # type: ignore[arg-type]
    assert provider_calls == []
    assert "CONFIRM" in answers[-1]

    message.text = f"/refundstars {charge_id} CONFIRM"
    await payment_handler.cmd_refund_stars(message)  # type: ignore[arg-type]

    assert provider_calls == [(user_id, charge_id)]
    assert "Stars возвращены" in answers[-1]
    assert preview_stars_refund(charge_id).status == "completed"


@pytest.mark.asyncio
async def test_ambiguous_provider_timeout_keeps_entitlement_held_for_safe_retry(monkeypatch) -> None:
    user_id = 782061
    charge_id = _charge("timeout")
    _pay(monkeypatch, user_id=user_id, package_id="practice_start_7", charge_id=charge_id)
    monkeypatch.setattr(payment_handler, "is_platform_admin", lambda _user_id: True)

    answers: list[str] = []

    class FakeBot:
        async def refund_star_payment(self, *, user_id: int, telegram_payment_charge_id: str) -> bool:
            raise asyncio.TimeoutError

    async def answer(text: str, **_kwargs) -> None:
        answers.append(text)

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=900001),
        text=f"/refundstars {charge_id} CONFIRM",
        answer=answer,
        bot=FakeBot(),
    )
    await payment_handler.cmd_refund_stars(message)  # type: ignore[arg-type]

    assert preview_stars_refund(charge_id).status == "prepared"
    assert get_wallet(user_id).available_tokens == 0
    assert "остаётся временно удержанным" in answers[-1]


def test_already_refunded_provider_error_accepts_bot_api_code() -> None:
    exc = RuntimeError("Bad Request: PAYMENT_ALREADY_REFUNDED")

    assert payment_handler._already_refunded_error(exc) is True


def test_refund_cannot_use_tokens_from_a_later_purchase(monkeypatch) -> None:
    user_id = 782071
    first_charge = _charge("first-used")
    second_charge = _charge("second-intact")
    _pay(monkeypatch, user_id=user_id, package_id="practice_start_7", charge_id=first_charge)

    reserved, _wallet, reservation_id = reserve_practice(user_id, audio_anchor=991071)
    assert reserved is True
    assert reservation_id
    # A later package restores the aggregate wallet above the first grant size.
    # Refundability must still be decided from the exact first payment lot.
    _pay(monkeypatch, user_id=user_id, package_id="practice_start_7", charge_id=second_charge)
    assert get_wallet(user_id).available_tokens >= 7

    first = preview_stars_refund(first_charge)
    second = preview_stars_refund(second_charge)

    assert first.refundable is False
    assert first.reason == "purchased_practices_already_used_or_reserved"
    assert second.refundable is True
