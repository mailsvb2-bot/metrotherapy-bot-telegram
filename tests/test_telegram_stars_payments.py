from __future__ import annotations

from types import SimpleNamespace

import pytest

from handlers import payments as payment_handler
from services.db import db
from services.gift_claims import claim_gift_token, create_gift_checkout_token
from services.payments import telegram_stars
from services.payments.telegram_stars import (
    STARS_CURRENCY,
    build_stars_payload,
    parse_stars_payload,
    record_successful_stars_payment,
    send_stars_invoice,
    validate_stars_pre_checkout,
)
from services.practice_token_contract import telegram_stars_price
from services.practice_tokens import get_wallet


def test_stars_payload_is_user_bound_and_amount_checked() -> None:
    payload = build_stars_payload(buyer_user_id=781001, package_id="practice_start_7")
    order = parse_stars_payload(payload)

    assert order.buyer_user_id == 781001
    assert order.package_id == "practice_start_7"
    assert order.gift_token == ""
    assert validate_stars_pre_checkout(
        payload=payload,
        user_id=781001,
        currency="XTR",
        total_amount=telegram_stars_price("practice_start_7"),
    ) is None
    assert validate_stars_pre_checkout(
        payload=payload,
        user_id=781002,
        currency="XTR",
        total_amount=telegram_stars_price("practice_start_7"),
    )
    assert validate_stars_pre_checkout(
        payload=payload,
        user_id=781001,
        currency="XTR",
        total_amount=1,
    )


def test_stars_price_can_be_changed_without_changing_ruble_price(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STARS_PRICE_PRACTICE_START_7", "1777")
    assert telegram_stars_price("practice_start_7") == 1777


def test_successful_stars_payment_grants_once_and_records_charge(monkeypatch) -> None:
    monkeypatch.setattr(telegram_stars, "log_event", lambda *args, **kwargs: None)
    user_id = 781011
    charge_id = "stars-charge-integration-781011"
    payload = build_stars_payload(buyer_user_id=user_id, package_id="practice_start_7")
    amount = telegram_stars_price("practice_start_7")

    first = record_successful_stars_payment(
        user_id=user_id,
        payload=payload,
        total_amount=amount,
        currency=STARS_CURRENCY,
        telegram_charge_id=charge_id,
    )
    second = record_successful_stars_payment(
        user_id=user_id,
        payload=payload,
        total_amount=amount,
        currency=STARS_CURRENCY,
        telegram_charge_id=charge_id,
    )

    assert first.completed is True
    assert first.duplicate is False
    assert first.wallet_balance == 7
    assert second.completed is True
    assert second.duplicate is True
    assert get_wallet(user_id).available_tokens == 7

    with db() as conn:
        payment = conn.execute(
            "SELECT currency, amount, processing_status, side_effects_done_at_utc FROM payments WHERE telegram_charge_id=?",
            (charge_id,),
        ).fetchone()
        grants = conn.execute(
            "SELECT COUNT(*) AS n FROM payment_token_grants WHERE provider='telegram_stars' AND provider_payment_id=?",
            (charge_id,),
        ).fetchone()
    assert payment["currency"] == "XTR"
    assert int(payment["amount"]) == amount
    assert payment["processing_status"] == "side_effects_done"
    assert payment["side_effects_done_at_utc"]
    assert int(grants["n"]) == 1


def test_stars_gift_marks_claim_without_crediting_buyer(monkeypatch) -> None:
    monkeypatch.setattr(telegram_stars, "log_event", lambda *args, **kwargs: None)
    buyer_id = 781021
    recipient_id = 781022
    token = create_gift_checkout_token(
        buyer_user_id=buyer_id,
        package_id="practice_start_7",
        source_platform="telegram",
    )
    payload = build_stars_payload(
        buyer_user_id=buyer_id,
        package_id="practice_start_7",
        gift_token=token,
    )

    result = record_successful_stars_payment(
        user_id=buyer_id,
        payload=payload,
        total_amount=telegram_stars_price("practice_start_7"),
        currency="XTR",
        telegram_charge_id="stars-gift-charge-781021",
    )

    assert result.gift_token == token
    assert get_wallet(buyer_id).available_tokens == 0
    claimed = claim_gift_token(
        gift_token=token,
        recipient_user_id=recipient_id,
        platform="telegram",
    )
    assert claimed.ok is True
    assert get_wallet(recipient_id).available_tokens == 7


@pytest.mark.asyncio
async def test_invoice_uses_xtr_and_empty_provider_token(monkeypatch) -> None:
    monkeypatch.setattr(telegram_stars, "log_event", lambda *args, **kwargs: None)
    captured = {}

    class FakeMessage:
        from_user = SimpleNamespace(id=781031)

        async def answer_invoice(self, **kwargs):
            captured.update(kwargs)

    token = await send_stars_invoice(
        FakeMessage(),  # type: ignore[arg-type]
        package_id="practice_start_7",
        as_gift=False,
    )

    assert token == ""
    assert captured["currency"] == "XTR"
    assert captured["provider_token"] == ""
    assert len(captured["prices"]) == 1
    assert captured["prices"][0].amount == telegram_stars_price("practice_start_7")
    assert parse_stars_payload(captured["payload"]).buyer_user_id == 781031


@pytest.mark.asyncio
async def test_non_xtr_pre_checkout_stays_on_legacy_path(monkeypatch) -> None:
    calls = []

    async def fake_legacy(pre):
        calls.append(pre)

    monkeypatch.setattr(payment_handler, "legacy_pre_checkout", fake_legacy)
    pre = SimpleNamespace(currency="RUB")
    await payment_handler._pre_checkout(pre)  # noqa: SLF001
    assert calls == [pre]


@pytest.mark.asyncio
async def test_non_xtr_successful_payment_stays_on_legacy_path(monkeypatch) -> None:
    calls = []

    async def fake_legacy(message):
        calls.append(message)

    monkeypatch.setattr(payment_handler, "legacy_successful_payment", fake_legacy)
    payment = SimpleNamespace(currency="RUB")
    message = SimpleNamespace(successful_payment=payment)
    await payment_handler._successful_payment(message)  # noqa: SLF001
    assert calls == [message]
