from __future__ import annotations

from services.payments import telegram_stars
from services.payments.telegram_stars import (
    build_stars_payload,
    parse_stars_payload,
    record_successful_stars_payment,
    validate_stars_pre_checkout,
)
from services.practice_tokens import get_wallet


def test_paid_invoice_keeps_its_pinned_price_after_catalog_change(monkeypatch) -> None:
    monkeypatch.setattr(telegram_stars, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "explicit")
    user_id = 781041
    payload = build_stars_payload(
        buyer_user_id=user_id,
        package_id="practice_start_7",
    )
    pinned = parse_stars_payload(payload).amount_xtr

    monkeypatch.setenv("TELEGRAM_STARS_PRICE_PRACTICE_START_7", str(pinned + 100))

    assert validate_stars_pre_checkout(
        payload=payload,
        user_id=user_id,
        currency="XTR",
        total_amount=pinned,
    ) is not None

    result = record_successful_stars_payment(
        user_id=user_id,
        payload=payload,
        total_amount=pinned,
        currency="XTR",
        telegram_charge_id="stars-price-race-781041",
    )

    assert result.completed is True
    assert result.duplicate is False
    assert get_wallet(user_id).available_tokens == 7
