from __future__ import annotations

import pytest
from services.db import db
from services.payments.checkout_intent import verify_checkout_intent
from services.payments.ui import kb_gift_tariffs, kb_telegram_gift_yookassa_checkout, kb_telegram_payment_methods
from services.practice_token_contract import public_practice_packages


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_telegram_gift_tariffs_defer_token_creation_until_payment_method(monkeypatch):
    user_id = 910100
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://metrotherapy.example")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "unit-test-checkout-signing-key")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")

    with db() as conn:
        conn.execute("DELETE FROM gift_claims WHERE buyer_user_id=?", (user_id,))

    markup = kb_gift_tariffs(user_id=user_id, back_cb="menu:main")
    buttons = _buttons(markup)
    package_callbacks = [
        str(button.callback_data)
        for button in buttons
        if str(button.callback_data or "").startswith("pay:gift_methods:")
    ]

    assert len(package_callbacks) == len(public_practice_packages())
    assert not any(button.url for button in buttons)
    assert package_callbacks == [
        f"pay:gift_methods:{package.package_id}"
        for package in public_practice_packages()
    ]

    # Opening the gift tariff keyboard must be side-effect free. The buyer-bound
    # gift token is created only after explicit terms acceptance and invoice creation.
    with db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM gift_claims WHERE buyer_user_id=?",
            (user_id,),
        ).fetchone()
    assert int(count["n"]) == 0


def test_gift_payment_choice_is_stars_only_and_has_no_checkout_side_effect(monkeypatch):
    user_id = 910101
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    with db() as conn:
        conn.execute("DELETE FROM gift_claims WHERE buyer_user_id=?", (user_id,))

    methods = _buttons(kb_telegram_payment_methods(user_id=user_id, package_id="practice_start_7", gift=True))
    assert any(button.callback_data == "stars:gift_terms:practice_start_7" for button in methods)
    assert not any(button.url for button in methods)
    assert not any(str(button.callback_data or "").startswith("yookassa:") for button in methods)
    with pytest.raises(ValueError, match="telegram_digital_yookassa_disabled"):
        kb_telegram_gift_yookassa_checkout(user_id=user_id, package_id="practice_start_7")

    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM gift_claims WHERE buyer_user_id=?", (user_id,)).fetchone()
    assert int(count["n"]) == 0
