from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

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
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")

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

    with db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM gift_claims WHERE buyer_user_id=?",
            (user_id,),
        ).fetchone()
    assert int(count["n"]) == 0


def test_gift_payment_choice_offers_stars_and_signed_yookassa(monkeypatch):
    user_id = 910101
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://metrotherapy.example")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "unit-test-checkout-signing-key")

    with db() as conn:
        conn.execute("DELETE FROM gift_claims WHERE buyer_user_id=?", (user_id,))

    methods = _buttons(kb_telegram_payment_methods(user_id=user_id, package_id="practice_start_7", gift=True))
    assert any(button.callback_data == "stars:gift_terms:practice_start_7" for button in methods)

    yookassa = next(button for button in methods if button.url and "ЮKassa" in button.text)
    query = parse_qs(urlsplit(yookassa.url).query)
    assert query["source"] == ["telegram"]
    assert query["user_id"] == [str(user_id)]
    assert query["package_id"] == ["practice_start_7"]
    assert query["gift_token"][0].startswith("gift_")
    assert verify_checkout_intent(
        query["intent"][0],
        expected_user_id=user_id,
        expected_package_id="practice_start_7",
        expected_kind="tokens",
        expected_source="telegram",
        expected_amount_minor=249900,
        expected_currency="RUB",
        expected_gift_token=query["gift_token"][0],
    )

    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM gift_claims WHERE buyer_user_id=?", (user_id,)).fetchone()
    assert int(count["n"]) == 1


def test_gift_yookassa_kill_switch_has_no_checkout_side_effect(monkeypatch):
    user_id = 910102
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "0")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://metrotherapy.example")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "unit-test-checkout-signing-key")

    with db() as conn:
        conn.execute("DELETE FROM gift_claims WHERE buyer_user_id=?", (user_id,))

    methods = _buttons(kb_telegram_payment_methods(user_id=user_id, package_id="practice_start_7", gift=True))
    assert any(button.callback_data == "stars:gift_terms:practice_start_7" for button in methods)
    assert not any(button.url for button in methods)
    assert any(button.callback_data == "tariffs:yookassa_disabled" for button in methods)

    with pytest.raises(ValueError, match="telegram_yookassa_disabled"):
        kb_telegram_gift_yookassa_checkout(user_id=user_id, package_id="practice_start_7")

    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM gift_claims WHERE buyer_user_id=?", (user_id,)).fetchone()
    assert int(count["n"]) == 0
