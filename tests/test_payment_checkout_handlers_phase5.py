from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from handlers import payments


class FakeMessage:
    def __init__(self, user_id: int | None = 7, *, successful_payment: Any = None) -> None:
        self.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
        self.successful_payment = successful_payment
        self.answers: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))


class FakeCallback:
    def __init__(self, data: str | None, message: Any, user_id: Any = 7) -> None:
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[tuple[tuple[Any, ...], dict[str, Any]]] = []


class FakePreCheckout:
    def __init__(self, *, currency: str = "XTR", answer_exc: BaseException | None = None) -> None:
        self.currency = currency
        self.invoice_payload = "payload"
        self.from_user = SimpleNamespace(id=7)
        self.total_amount = 50
        self.answer_exc = answer_exc
        self.answers: list[dict[str, Any]] = []

    async def answer(self, **kwargs: Any) -> None:
        self.answers.append(kwargs)
        if self.answer_exc:
            raise self.answer_exc


async def direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


def patch_message_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payments, "Message", FakeMessage)


async def safe_callback(cb: FakeCallback, *args: Any, **kwargs: Any) -> None:
    cb.answers.append((args, kwargs))


def test_checkout_helper_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    assert payments._message_user_id(FakeMessage(7)) == 7
    assert payments._message_user_id(FakeMessage(None)) is None
    assert payments._package_id("stars:buy:pkg", "stars:buy:") == "pkg"
    for value in (None, "bad:pkg", "stars:buy:"):
        with pytest.raises(ValueError, match="stars_package_callback_invalid"):
            payments._package_id(value, "stars:buy:")

    patch_message_type(monkeypatch)
    message = FakeMessage()
    assert payments._callback_message(FakeCallback("x", message)) is message
    assert payments._callback_message(FakeCallback("x", object())) is None


@pytest.mark.asyncio
async def test_terms_and_method_callbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_message_type(monkeypatch)
    monkeypatch.setattr(payments, "safe_answer_callback", safe_callback)
    monkeypatch.setattr(payments, "kb_back", lambda target: f"back:{target}")
    monkeypatch.setattr(payments, "payment_terms_text", lambda: "terms")
    monkeypatch.setattr(
        payments,
        "payment_terms_keyboard",
        lambda package_id, as_gift: (package_id, as_gift),
    )

    no_message = FakeCallback("stars:terms:pkg", object())
    await payments._show_stars_terms(no_message, as_gift=False)
    assert no_message.answers

    invalid = FakeMessage()
    await payments._show_stars_terms(FakeCallback("bad", invalid), as_gift=False)
    assert "Пакет не найден" in invalid.answers[-1][0]

    valid = FakeMessage()
    await payments._show_stars_terms(FakeCallback("stars:gift_terms:pkg", valid), as_gift=True)
    assert valid.answers[-1] == ("terms", {"reply_markup": ("pkg", True)})

    monkeypatch.setattr(payments, "telegram_payment_method_text", lambda package: f"method:{package}")
    monkeypatch.setattr(
        payments,
        "kb_telegram_payment_methods",
        lambda user_id, package_id, gift: (user_id, package_id, gift),
    )
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(payments, "log_event", lambda *args: events.append(args))

    methods = FakeMessage()
    await payments._show_payment_methods(FakeCallback("pay:methods:pkg", methods), as_gift=False)
    assert methods.answers[-1] == ("method:pkg", {"reply_markup": (7, "pkg", False)})
    assert events[-1][1] == "payment_method_choice_opened"

    invalid_methods = FakeMessage()
    await payments._show_payment_methods(FakeCallback("bad", invalid_methods), as_gift=True)
    assert "Выберите пакет заново" in invalid_methods.answers[-1][0]


@pytest.mark.asyncio
async def test_invoice_callback_success_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_message_type(monkeypatch)
    monkeypatch.setattr(payments, "safe_answer_callback", safe_callback)
    monkeypatch.setattr(payments, "kb_back", lambda target: target)
    calls: list[dict[str, Any]] = []

    async def send(_message: Any, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(payments, "send_stars_invoice", send)
    await payments._send_stars_from_callback(
        FakeCallback("stars:gift:pkg", FakeMessage()), as_gift=True
    )
    assert calls == [{"package_id": "pkg", "as_gift": True}]

    async def payment_error(*_args: Any, **_kwargs: Any) -> None:
        raise payments.StarsPaymentError("bad")

    monkeypatch.setattr(payments, "send_stars_invoice", payment_error)
    failed = FakeMessage()
    await payments._send_stars_from_callback(
        FakeCallback("stars:buy:pkg", failed), as_gift=False
    )
    assert "Не удалось создать счёт" in failed.answers[-1][0]

    class ApiError(Exception):
        pass

    monkeypatch.setattr(payments, "TelegramAPIError", ApiError)

    async def api_error(*_args: Any, **_kwargs: Any) -> None:
        raise ApiError("api")

    monkeypatch.setattr(payments, "send_stars_invoice", api_error)
    unavailable = FakeMessage()
    await payments._send_stars_from_callback(
        FakeCallback("stars:buy:pkg", unavailable), as_gift=False
    )
    assert "временно не создал" in unavailable.answers[-1][0]


@pytest.mark.asyncio
async def test_disabled_and_information_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    patch_message_type(monkeypatch)
    monkeypatch.setattr(payments, "safe_answer_callback", safe_callback)
    monkeypatch.setattr(payments, "payment_terms_text", lambda: "terms")
    monkeypatch.setattr(payments, "payment_support_contact", lambda: "@support")
    monkeypatch.setattr(payments, "kb_back", lambda target: f"back:{target}")

    terms = FakeMessage()
    await payments._terms(terms)
    assert terms.answers == [("terms", {})]

    support = FakeMessage()
    await payments._pay_support(support)
    assert "@support" in support.answers[0][0]

    overview = FakeMessage()
    await payments._stars_terms_overview(FakeCallback("stars:terms", overview))
    assert overview.answers == [("terms", {"reply_markup": "back:sub:menu"})]

    for handler, data in (
        (payments._yookassa_gift, "yookassa:gift:pkg"),
        (payments._stars_disabled, "tariffs:stars_disabled"),
        (payments._public_base_missing, "tariffs:public_base_missing"),
        (payments._yookassa_disabled, "tariffs:yookassa_disabled"),
    ):
        cb = FakeCallback(data, FakeMessage())
        await handler(cb)
        assert cb.answers[-1][1]["show_alert"] is True

    for handler, data in (
        (payments._sub_pick_disabled, "sub:buy:1:1"),
        (payments._pay_selected_disabled, "pay:selected"),
        (payments._gift_buy_disabled, "gift:buy:1:1"),
    ):
        message = FakeMessage()
        await handler(FakeCallback(data, message))
        assert payments._DISABLED in message.answers[-1][0]


@pytest.mark.asyncio
async def test_pre_checkout_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class ApiError(Exception):
        pass

    monkeypatch.setattr(payments, "TelegramAPIError", ApiError)
    legacy = FakePreCheckout(currency="RUB")
    await payments._pre_checkout(legacy)
    assert legacy.answers[-1]["ok"] is False

    failed_answer = FakePreCheckout(currency="RUB", answer_exc=ApiError("api"))
    await payments._pre_checkout(failed_answer)

    monkeypatch.setattr(payments.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(payments, "validate_stars_pre_checkout", lambda **_kwargs: None)
    valid = FakePreCheckout(currency="XTR")
    await payments._pre_checkout(valid)
    assert valid.answers[-1] == {"ok": True, "error_message": None}

    monkeypatch.setattr(payments, "validate_stars_pre_checkout", lambda **_kwargs: "bad purchase")
    invalid = FakePreCheckout(currency="XTR")
    await payments._pre_checkout(invalid)
    assert invalid.answers[-1] == {"ok": False, "error_message": "bad purchase"}


@pytest.mark.asyncio
async def test_successful_payment_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    legacy_calls: list[Any] = []

    async def legacy(message: Any) -> None:
        legacy_calls.append(message)

    monkeypatch.setattr(payments, "legacy_successful_payment", legacy)
    rub = FakeMessage(successful_payment=SimpleNamespace(currency="RUB"))
    await payments._successful_payment(rub)
    assert legacy_calls == [rub]

    no_user = FakeMessage(None, successful_payment=SimpleNamespace(currency="XTR"))
    await payments._successful_payment(no_user)
    assert no_user.answers == []

    payment = SimpleNamespace(
        currency="XTR",
        invoice_payload="payload",
        total_amount=50,
        telegram_payment_charge_id="charge",
        provider_payment_charge_id="provider",
    )
    monkeypatch.setattr(payments.asyncio, "to_thread", direct_to_thread)
    recovery: list[Any] = []

    async def recover(message: Any) -> None:
        recovery.append(message)

    monkeypatch.setattr(payments, "_answer_stars_manual_recovery", recover)
    monkeypatch.setattr(
        payments,
        "record_successful_stars_payment",
        lambda **_kwargs: (_ for _ in ()).throw(payments.StarsPaymentError("bad")),
    )
    failed = FakeMessage(successful_payment=payment)
    await payments._successful_payment(failed)
    assert recovery == [failed]

    monkeypatch.setattr(
        payments,
        "record_successful_stars_payment",
        lambda **_kwargs: SimpleNamespace(duplicate=True, gift_token="", wallet_balance=None),
    )
    duplicate = FakeMessage(successful_payment=payment)
    await payments._successful_payment(duplicate)
    assert duplicate.answers == []

    gifts: list[str] = []

    async def deliver(_message: Any, code: str) -> None:
        gifts.append(code)

    monkeypatch.setattr(payments, "deliver_gift_message", deliver)
    monkeypatch.setattr(
        payments,
        "record_successful_stars_payment",
        lambda **_kwargs: SimpleNamespace(duplicate=False, gift_token="gift_abc", wallet_balance=None),
    )
    gift = FakeMessage(successful_payment=payment)
    await payments._successful_payment(gift)
    assert gifts == ["abc"]

    monkeypatch.setattr(payments, "kb_after_paid", lambda: "after")
    monkeypatch.setattr(
        payments,
        "record_successful_stars_payment",
        lambda **_kwargs: SimpleNamespace(duplicate=False, gift_token="", wallet_balance=12),
    )
    paid = FakeMessage(successful_payment=payment)
    await payments._successful_payment(paid)
    assert "На балансе: 12 практик" in paid.answers[-1][0]
    assert paid.answers[-1][1]["reply_markup"] == "after"
