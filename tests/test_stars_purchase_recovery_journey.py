from __future__ import annotations

from types import SimpleNamespace

import pytest

from handlers import payments as payment_handlers
from services.payments.stars_invoice_transport import PREMIUM_BOT_URL, _invoice_keyboard
from services.payments.terms import payment_terms_keyboard
from services.payments.ui import kb_tariffs, kb_telegram_payment_methods


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _callback(markup, prefix: str) -> str:
    return next(
        str(button.callback_data)
        for button in _buttons(markup)
        if button.callback_data and str(button.callback_data).startswith(prefix)
    )


def test_personal_purchase_journey_keeps_package_context_through_stars_recovery(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "0")
    package_id = "practice_start_7"

    tariffs = kb_tariffs(user_id=730001)
    assert _callback(tariffs, "pay:methods:") == f"pay:methods:{package_id}"

    methods = kb_telegram_payment_methods(user_id=730001, package_id=package_id)
    assert _callback(methods, "stars:terms:") == f"stars:terms:{package_id}"

    terms = payment_terms_keyboard(package_id=package_id, as_gift=False)
    assert _callback(terms, "stars:buy:") == f"stars:buy:{package_id}"

    invoice = _invoice_keyboard(
        url="https://t.me/$invoice-personal",
        amount_xtr=1226,
        package_id=package_id,
        as_gift=False,
    )
    buttons = _buttons(invoice)
    assert buttons[0].url == "https://t.me/$invoice-personal"
    assert buttons[1].url == PREMIUM_BOT_URL
    assert buttons[2].callback_data == f"stars:buy:{package_id}"
    assert buttons[3].callback_data == f"pay:methods:{package_id}"


def test_gift_purchase_journey_keeps_gift_context_through_stars_recovery(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "0")
    package_id = "practice_start_7"

    methods = kb_telegram_payment_methods(user_id=730002, package_id=package_id, gift=True)
    assert _callback(methods, "stars:gift_terms:") == f"stars:gift_terms:{package_id}"

    terms = payment_terms_keyboard(package_id=package_id, as_gift=True)
    assert _callback(terms, "stars:gift:") == f"stars:gift:{package_id}"

    invoice = _invoice_keyboard(
        url="https://t.me/$invoice-gift",
        amount_xtr=1226,
        package_id=package_id,
        as_gift=True,
    )
    buttons = _buttons(invoice)
    assert buttons[1].url == PREMIUM_BOT_URL
    assert buttons[2].callback_data == f"stars:gift:{package_id}"
    assert buttons[3].callback_data == f"pay:gift_methods:{package_id}"


@pytest.mark.asyncio
async def test_return_after_buying_stars_creates_a_fresh_personal_invoice(monkeypatch) -> None:
    sent: list[tuple[str, bool]] = []

    async def fake_safe_answer(_cb, *_args, **_kwargs):
        return None

    async def fake_send(_message, *, package_id: str, as_gift: bool = False):
        sent.append((package_id, as_gift))
        return ""

    message = SimpleNamespace(answer=lambda *_args, **_kwargs: None)
    cb = SimpleNamespace(data="stars:buy:practice_start_7")
    monkeypatch.setattr(payment_handlers, "safe_answer_callback", fake_safe_answer)
    monkeypatch.setattr(payment_handlers, "_callback_message", lambda _cb: message)
    monkeypatch.setattr(payment_handlers, "send_stars_invoice", fake_send)

    await payment_handlers._send_stars_from_callback(cb, as_gift=False)
    await payment_handlers._send_stars_from_callback(cb, as_gift=False)

    assert sent == [
        ("practice_start_7", False),
        ("practice_start_7", False),
    ]


@pytest.mark.asyncio
async def test_return_after_buying_stars_creates_a_fresh_gift_invoice(monkeypatch) -> None:
    sent: list[tuple[str, bool]] = []

    async def fake_safe_answer(_cb, *_args, **_kwargs):
        return None

    async def fake_send(_message, *, package_id: str, as_gift: bool = False):
        sent.append((package_id, as_gift))
        return "gift_test"

    message = SimpleNamespace(answer=lambda *_args, **_kwargs: None)
    cb = SimpleNamespace(data="stars:gift:practice_start_7")
    monkeypatch.setattr(payment_handlers, "safe_answer_callback", fake_safe_answer)
    monkeypatch.setattr(payment_handlers, "_callback_message", lambda _cb: message)
    monkeypatch.setattr(payment_handlers, "send_stars_invoice", fake_send)

    await payment_handlers._send_stars_from_callback(cb, as_gift=True)

    assert sent == [("practice_start_7", True)]
