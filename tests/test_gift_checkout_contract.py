from __future__ import annotations

import pytest
from services.db import db
from services.payments.ui import kb_gift_tariffs, kb_telegram_gift_yookassa_checkout, kb_telegram_payment_methods
from services.practice_token_contract import public_practice_packages


TOPUP_URL = "tg://stars_topup?balance=1500&purpose=metrotherapy_practice_start_7"


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_telegram_gift_tariffs_defer_token_creation_until_invoice(monkeypatch):
    user_id = 910100
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
    assert not any("ЮKassa" in button.text or "₽" in button.text for button in buttons)
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


def test_gift_choice_offers_existing_stars_or_topup_without_side_effect(monkeypatch):
    user_id = 910101
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")

    with db() as conn:
        conn.execute("DELETE FROM gift_claims WHERE buyer_user_id=?", (user_id,))

    buttons = _buttons(kb_telegram_payment_methods(user_id=user_id, package_id="practice_start_7", gift=True))

    assert buttons[0].callback_data == "stars:gift_terms:practice_start_7"
    assert buttons[0].text == "⭐ У меня уже есть Stars — оплатить подарок"
    assert buttons[1].url == TOPUP_URL
    assert buttons[2].callback_data == "stars:gift_terms:practice_start_7"
    assert buttons[2].text == "✅ Stars куплены — оплатить подарок"
    assert not any("ЮKassa" in button.text or "₽" in button.text for button in buttons)

    with db() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM gift_claims WHERE buyer_user_id=?", (user_id,)).fetchone()
    assert int(count["n"]) == 0


def test_legacy_telegram_gift_yookassa_entrypoint_is_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")

    with pytest.raises(ValueError, match="telegram_yookassa_disabled"):
        kb_telegram_gift_yookassa_checkout(user_id=910102, package_id="practice_start_7")
