from __future__ import annotations

from services.db import db
from services.payments import telegram_stars
from services.payments.telegram_stars import (
    build_stars_payload,
    record_successful_stars_payment,
)
from services.practice_token_contract import telegram_stars_price
from services.practice_tokens import get_wallet


def test_personal_package_stars_grants_tokens_and_premium_once(monkeypatch) -> None:
    monkeypatch.setattr(telegram_stars, "log_event", lambda *args, **kwargs: None)
    user_id = 781051
    charge_id = "stars-personal-premium-781051"
    package_id = "practice_personal_month"
    payload = build_stars_payload(buyer_user_id=user_id, package_id=package_id)
    amount = telegram_stars_price(package_id)

    first = record_successful_stars_payment(
        user_id=user_id,
        payload=payload,
        total_amount=amount,
        currency="XTR",
        telegram_charge_id=charge_id,
    )
    second = record_successful_stars_payment(
        user_id=user_id,
        payload=payload,
        total_amount=amount,
        currency="XTR",
        telegram_charge_id=charge_id,
    )

    assert first.completed is True
    assert first.wallet_balance == 60
    assert first.consultation_request_created is True
    assert second.duplicate is True
    assert get_wallet(user_id).available_tokens == 60

    with db() as conn:
        entitlements = conn.execute(
            "SELECT COUNT(*) AS n FROM premium_entitlements WHERE provider='telegram_stars' AND provider_payment_id=?",
            (charge_id,),
        ).fetchone()
        consultations = conn.execute(
            "SELECT COUNT(*) AS n FROM consultation_requests WHERE provider='telegram_stars' AND provider_payment_id=?",
            (charge_id,),
        ).fetchone()
        outbox = conn.execute(
            "SELECT COUNT(*) AS n FROM premium_delivery_outbox WHERE idempotency_key LIKE ?",
            (f"premium_delivery:telegram_stars:{charge_id}:%",),
        ).fetchone()

    assert int(entitlements["n"]) == 2
    assert int(consultations["n"]) == 1
    assert int(outbox["n"]) >= 2
