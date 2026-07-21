from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from handlers import payments


class FakeMessage:
    def __init__(
        self,
        user_id: int | None = 7,
        *,
        text: str | None = None,
        successful_payment: Any = None,
    ) -> None:
        self.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
        self.text = text
        self.successful_payment = successful_payment
        self.answers: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))


class FakeCallback:
    def __init__(self, data: str | None, message: Any) -> None:
        self.data = data
        self.message = message
        self.from_user = SimpleNamespace(id=7)
        self.answers: list[tuple[tuple[Any, ...], dict[str, Any]]] = []


class FakePreCheckout:
    def __init__(self, exc: BaseException | None = None) -> None:
        self.currency = "XTR"
        self.invoice_payload = "payload"
        self.from_user = SimpleNamespace(id=7)
        self.total_amount = 50
        self.exc = exc

    async def answer(self, **_kwargs: Any) -> None:
        if self.exc:
            raise self.exc


async def direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


async def safe_callback(cb: FakeCallback, *args: Any, **kwargs: Any) -> None:
    cb.answers.append((args, kwargs))


def refund_plan(**kwargs: Any) -> payments.StarsRefundPlan:
    values = {
        "telegram_charge_id": "charge",
        "payment_user_id": 7,
        "package_id": "pkg",
        "status": "new",
        "refundable": True,
    }
    values.update(kwargs)
    return payments.StarsRefundPlan(**values)


@pytest.mark.asyncio
async def test_refund_command_early_exit_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payments, "is_platform_admin", lambda _uid: True)
    message = FakeMessage(text="/refundstars charge CONFIRM")

    async def none_plan(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(payments, "_load_refund_plan", none_plan)
    await payments.cmd_refund_stars(message)

    current = refund_plan()

    async def load(*_args: Any, **_kwargs: Any) -> Any:
        return current

    async def blocked(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(payments, "_load_refund_plan", load)
    monkeypatch.setattr(payments, "_refund_plan_allows_execution", blocked)
    await payments.cmd_refund_stars(message)

    async def allowed(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(payments, "_refund_plan_allows_execution", allowed)
    monkeypatch.setattr(payments, "_prepare_refund_plan", none_plan)
    await payments.cmd_refund_stars(message)

    async def prepared(*_args: Any, **_kwargs: Any) -> Any:
        return current

    monkeypatch.setattr(payments, "_prepare_refund_plan", prepared)
    monkeypatch.setattr(payments, "_run_provider_refund", blocked)
    await payments.cmd_refund_stars(message)

    async def provider_ok(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(payments, "_run_provider_refund", provider_ok)
    monkeypatch.setattr(payments, "_complete_refund", none_plan)
    await payments.cmd_refund_stars(message)


@pytest.mark.asyncio
async def test_callback_helpers_without_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payments, "Message", FakeMessage)
    monkeypatch.setattr(payments, "safe_answer_callback", safe_callback)

    callback = FakeCallback("pay:methods:pkg", object())
    await payments._show_payment_methods(callback, as_gift=False)
    assert callback.answers

    callback = FakeCallback("stars:buy:pkg", object())
    await payments._send_stars_from_callback(callback, as_gift=False)
    assert callback.answers

    callback = FakeCallback("stars:terms", object())
    await payments._stars_terms_overview(callback)
    assert callback.answers

    for handler, data in (
        (payments._sub_pick_disabled, "sub:buy:1:1"),
        (payments._pay_selected_disabled, "pay:selected"),
        (payments._gift_buy_disabled, "gift:buy:1:1"),
    ):
        callback = FakeCallback(data, object())
        await handler(callback)
        assert callback.answers


@pytest.mark.asyncio
async def test_manual_recovery_and_router_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    message = FakeMessage()
    await payments._answer_stars_manual_recovery(message)
    assert "автоматическое начисление не завершилось" in message.answers[0][0]

    calls: list[tuple[str, Any]] = []

    async def record(name: str, value: Any) -> None:
        calls.append((name, value))

    monkeypatch.setattr(payments, "gift_pick_cancel", lambda value: record("cancel", value))
    monkeypatch.setattr(payments, "cmd_subscribe", lambda value: record("subscribe", value))
    callback = FakeCallback("x", message)
    monkeypatch.setattr(payments, "safe_answer_callback", safe_callback)
    monkeypatch.setattr(payments, "sub_menu", lambda value: record("sub_menu", value))
    monkeypatch.setattr(payments, "gift_menu", lambda value: record("gift_menu", value))
    monkeypatch.setattr(payments, "gift_pick_target", lambda value: record("gift_target", value))
    monkeypatch.setattr(payments, "gift_users_shared", lambda value, state: record("shared", (value, state)))

    await payments._gift_pick_cancel(message)
    await payments._cmd_subscribe(message)
    await payments._sub_menu(callback)
    await payments._gift_menu(callback)
    await payments._gift_pick_target(callback)
    await payments._gift_users_shared(message, "state")

    assert {name for name, _value in calls} == {
        "cancel",
        "subscribe",
        "sub_menu",
        "gift_menu",
        "gift_target",
        "shared",
    }


@pytest.mark.asyncio
async def test_callback_delegation_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []

    async def methods(_cb: Any, *, as_gift: bool) -> None:
        calls.append(("methods", as_gift))

    async def terms(_cb: Any, *, as_gift: bool) -> None:
        calls.append(("terms", as_gift))

    async def invoice(_cb: Any, *, as_gift: bool) -> None:
        calls.append(("invoice", as_gift))

    monkeypatch.setattr(payments, "_show_payment_methods", methods)
    monkeypatch.setattr(payments, "_show_stars_terms", terms)
    monkeypatch.setattr(payments, "_send_stars_from_callback", invoice)
    callback = FakeCallback("x", FakeMessage())

    await payments._payment_methods(callback)
    await payments._gift_payment_methods(callback)
    await payments._stars_terms(callback)
    await payments._stars_gift_terms(callback)
    await payments._stars_buy(callback)
    await payments._stars_gift(callback)

    assert calls == [
        ("methods", False),
        ("methods", True),
        ("terms", False),
        ("terms", True),
        ("invoice", False),
        ("invoice", True),
    ]


@pytest.mark.asyncio
async def test_precheckout_answer_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class ApiError(Exception):
        pass

    monkeypatch.setattr(payments, "TelegramAPIError", ApiError)
    monkeypatch.setattr(payments.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(payments, "validate_stars_pre_checkout", lambda **_kwargs: None)
    await payments._pre_checkout(FakePreCheckout(ApiError("answer failed")))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exception",
    [sqlite3.OperationalError("db"), ValueError("value"), OSError("os")],
)
async def test_successful_payment_recovery_exception_classes(
    monkeypatch: pytest.MonkeyPatch,
    exception: BaseException,
) -> None:
    payment = SimpleNamespace(
        currency="XTR",
        invoice_payload="payload",
        total_amount=50,
        telegram_payment_charge_id="charge",
        provider_payment_charge_id="provider",
    )
    monkeypatch.setattr(payments.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(
        payments,
        "record_successful_stars_payment",
        lambda **_kwargs: (_ for _ in ()).throw(exception),
    )
    recovered: list[Any] = []

    async def recovery(message: Any) -> None:
        recovered.append(message)

    monkeypatch.setattr(payments, "_answer_stars_manual_recovery", recovery)
    message = FakeMessage(successful_payment=payment)
    await payments._successful_payment(message)
    assert recovered == [message]


@pytest.mark.asyncio
async def test_successful_payment_without_wallet_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    payment = SimpleNamespace(
        currency="XTR",
        invoice_payload="payload",
        total_amount=50,
        telegram_payment_charge_id="charge",
        provider_payment_charge_id="provider",
    )
    monkeypatch.setattr(payments.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(
        payments,
        "record_successful_stars_payment",
        lambda **_kwargs: SimpleNamespace(duplicate=False, gift_token="", wallet_balance=None),
    )
    monkeypatch.setattr(payments, "kb_after_paid", lambda: "after")
    message = FakeMessage(successful_payment=payment)
    await payments._successful_payment(message)
    assert "На балансе" not in message.answers[0][0]
