from services.payments.hooks import payment_insert_values


def test_payment_insert_values_preserves_attribution_order():
    values = payment_insert_values(
        user_id=123,
        telegram_charge_id="tg-charge",
        provider_charge_id="provider-charge",
        payload="sub:7",
        amount=99000,
        currency="RUB",
        created_at="2026-05-11T12:00:00",
        decision_id="decision-1",
        correlation_id="corr-1",
    )

    assert values == (
        123,
        "tg-charge",
        "provider-charge",
        "sub:7",
        99000,
        "RUB",
        "2026-05-11T12:00:00",
        "decision-1",
        "corr-1",
    )

    assert values[6] == "2026-05-11T12:00:00"
    assert values[7] == "decision-1"
    assert values[8] == "corr-1"
