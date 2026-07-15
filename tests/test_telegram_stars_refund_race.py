from __future__ import annotations

import uuid

import pytest

from services.db import db
from services.payments import telegram_stars, telegram_stars_refunds
from services.payments.telegram_stars import build_stars_payload, record_successful_stars_payment
from services.practice_token_contract import telegram_stars_price
from services.practice_tokens import get_wallet


def test_prepare_refund_rechecks_delivery_state_inside_transaction(monkeypatch):
    user_id = 783101
    charge_id = f"stars-refund-race-{uuid.uuid4().hex}"
    monkeypatch.setattr(telegram_stars, "log_event", lambda *args, **kwargs: None)
    payload = build_stars_payload(
        buyer_user_id=user_id,
        package_id="practice_antistress_60",
    )
    record_successful_stars_payment(
        user_id=user_id,
        payload=payload,
        total_amount=telegram_stars_price("practice_antistress_60"),
        currency="XTR",
        telegram_charge_id=charge_id,
    )

    stale_plan = telegram_stars_refunds.preview_stars_refund(charge_id)
    assert stale_plan.refundable is True
    original_balance = get_wallet(user_id).available_tokens

    with db() as conn:
        conn.execute(
            "UPDATE premium_delivery_outbox SET status='processing' "
            "WHERE user_id=? AND idempotency_key LIKE ?",
            (user_id, f"premium_delivery:telegram_stars:{charge_id}:%"),
        )

    monkeypatch.setattr(
        telegram_stars_refunds,
        "preview_stars_refund",
        lambda _charge_id: stale_plan,
    )

    with pytest.raises(
        telegram_stars_refunds.StarsRefundError,
        match="premium_content_already_delivered",
    ):
        telegram_stars_refunds.prepare_stars_refund(charge_id, requested_by=900001)

    assert get_wallet(user_id).available_tokens == original_balance
    with db() as conn:
        refund = conn.execute(
            "SELECT status FROM telegram_stars_refunds WHERE telegram_charge_id=?",
            (charge_id,),
        ).fetchone()
    assert refund is None
