from __future__ import annotations

from services.db import db
from services.payments.ui import kb_gift_tariffs
from services.practice_token_contract import public_practice_packages


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_telegram_gift_tariffs_defer_token_creation_until_stars_invoice(monkeypatch):
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
        if str(button.callback_data or "").startswith("stars:gift_terms:")
    ]

    assert len(package_callbacks) == len(public_practice_packages())
    assert not any(button.url for button in buttons)
    assert package_callbacks == [
        f"stars:gift_terms:{package.package_id}"
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
