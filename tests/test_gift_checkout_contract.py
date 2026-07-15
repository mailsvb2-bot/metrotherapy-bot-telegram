from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from services.db import db
from services.payments.checkout_intent import verify_checkout_intent
from services.payments.ui import (
    kb_gift_tariffs,
    kb_telegram_gift_yookassa_checkout,
    kb_telegram_payment_methods,
)
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


def test_gift_payment_choice_defers_yookassa_token_until_explicit_choice(monkeypatch):
    user_id = 910101
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://metrotherapy.example")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "unit-test-checkout-signing-key")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")

    with db() as conn:
        conn.execute("DELETE FROM gift_claims WHERE buyer_user_id=?", (user_id,))

    methods = _buttons(
        kb_telegram_payment_methods(
            user_id=user_id,
            package_id="practice_start_7",
            gift=True,
        )
    )
    assert any(button.callback_data == "stars:gift_terms:practice_start_7" for button in methods)
    assert any(button.callback_data == "yookassa:gift:practice_start_7" for button in methods)
    assert not any(button.url for button in methods)

    with db() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM gift_claims WHERE buyer_user_id=?",
            (user_id,),
        ).fetchone()
    assert int(before["n"]) == 0

    checkout = _buttons(
        kb_telegram_gift_yookassa_checkout(
            user_id=user_id,
            package_id="practice_start_7",
        )
    )
    urls = [str(button.url) for button in checkout if button.url]
    assert len(urls) == 1
    params = parse_qs(urlsplit(urls[0]).query)
    gift_token = params["gift_token"][0]
    assert gift_token.startswith("gift_")
    verify_checkout_intent(
        params["intent"][0],
        expected_user_id=user_id,
        expected_package_id="practice_start_7",
        expected_gift_token=gift_token,
    )

    with db() as conn:
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM gift_claims WHERE buyer_user_id=?",
            (user_id,),
        ).fetchone()
    assert int(after["n"]) == 1
