from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from handlers import payments


class FakeMessage:
    def __init__(self, user_id: int | None = 7, *, text: str | None = None, bot: Any = None) -> None:
        self.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
        self.text = text
        self.bot = bot
        self.answers: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))


async def direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


def plan(**kwargs: Any) -> payments.StarsRefundPlan:
    values = {
        "telegram_charge_id": "charge-1",
        "payment_user_id": 7,
        "beneficiary_user_id": 7,
        "package_id": "pkg",
        "tokens": 7,
        "status": "new",
        "refundable": True,
    }
    values.update(kwargs)
    return payments.StarsRefundPlan(**values)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("/refundstars", None),
        ("/refundstars charge", ("charge", False)),
        ("/refundstars charge confirm", ("charge", True)),
        ("/refundstars charge no", ("charge", False)),
    ],
)
def test_refund_command_parsing(text: str | None, expected: Any) -> None:
    assert payments._refund_command_args(text) == expected


def test_already_refunded_detection() -> None:
    assert payments._already_refunded_error(RuntimeError("already_refunded"))
    assert payments._already_refunded_error(RuntimeError("Payment was refunded"))
    assert not payments._already_refunded_error(RuntimeError("other"))


@pytest.mark.asyncio
async def test_refund_storage_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payments.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(payments, "cancel_prepared_stars_refund", lambda charge_id, error: None)
    assert await payments._cancel_refund_hold("c", "e") is True

    monkeypatch.setattr(
        payments,
        "cancel_prepared_stars_refund",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(payments.StarsRefundError("bad")),
    )
    assert await payments._cancel_refund_hold("c", "e") is False

    message = FakeMessage()
    monkeypatch.setattr(payments, "preview_stars_refund", lambda charge: plan(telegram_charge_id=charge))
    loaded = await payments._load_refund_plan(message, "c")
    assert loaded is not None and loaded.telegram_charge_id == "c"

    monkeypatch.setattr(
        payments,
        "preview_stars_refund",
        lambda _charge: (_ for _ in ()).throw(payments.StarsRefundError("bad")),
    )
    assert await payments._load_refund_plan(message, "c") is None
    assert "Не удалось проверить" in message.answers[-1][0]

    monkeypatch.setattr(
        payments,
        "prepare_stars_refund",
        lambda charge, requested_by: plan(telegram_charge_id=charge, payment_user_id=requested_by),
    )
    prepared = await payments._prepare_refund_plan(message, "c", 9)
    assert prepared is not None and prepared.payment_user_id == 9

    monkeypatch.setattr(
        payments,
        "prepare_stars_refund",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(payments.StarsRefundError("bad")),
    )
    assert await payments._prepare_refund_plan(message, "c", 9) is None
    assert "Доступ пользователя не изменён" in message.answers[-1][0]

    monkeypatch.setattr(payments, "complete_stars_refund", lambda charge: plan(telegram_charge_id=charge, status="completed"))
    completed = await payments._complete_refund(message, "c")
    assert completed is not None and completed.status == "completed"

    monkeypatch.setattr(
        payments,
        "complete_stars_refund",
        lambda _charge: (_ for _ in ()).throw(payments.StarsRefundError("bad")),
    )
    assert await payments._complete_refund(message, "c") is None
    assert "локальный доступ ещё не отозван" in message.answers[-1][0]


@pytest.mark.asyncio
async def test_refund_execution_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payments, "refund_plan_text", lambda value: f"plan:{value.status}")

    message = FakeMessage()
    assert not await payments._refund_plan_allows_execution(
        message, plan(status="completed"), confirmed=True
    )
    assert "уже завершён" in message.answers[-1][0]

    preview = FakeMessage()
    assert not await payments._refund_plan_allows_execution(
        preview, plan(refundable=True), confirmed=False
    )
    assert "CONFIRM" in preview.answers[-1][0]

    blocked = FakeMessage()
    assert not await payments._refund_plan_allows_execution(
        blocked, plan(refundable=False), confirmed=True
    )
    assert "заблокирован" in blocked.answers[-1][0]

    assert await payments._refund_plan_allows_execution(
        FakeMessage(), plan(refundable=True), confirmed=True
    )


@pytest.mark.asyncio
async def test_provider_refund_success_false_and_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payments.asyncio, "to_thread", direct_to_thread)
    assert await payments._run_provider_refund(FakeMessage(bot=object()), plan(status="provider_refunded"))

    class Bot:
        def __init__(self, result: bool = True, exc: BaseException | None = None) -> None:
            self.result = result
            self.exc = exc

        async def refund_star_payment(self, **_kwargs: Any) -> bool:
            if self.exc:
                raise self.exc
            return self.result

    marks: list[str] = []
    monkeypatch.setattr(payments, "mark_stars_refund_provider_succeeded", lambda charge: marks.append(charge))
    success = FakeMessage(bot=Bot(True))
    assert await payments._run_provider_refund(success, plan())
    assert marks == ["charge-1"]

    monkeypatch.setattr(payments, "cancel_prepared_stars_refund", lambda *_args, **_kwargs: None)
    false_result = FakeMessage(bot=Bot(False))
    assert not await payments._run_provider_refund(false_result, plan())
    assert "Telegram не подтвердил" in false_result.answers[-1][0]

    class BadRequest(Exception):
        pass

    monkeypatch.setattr(payments, "TelegramBadRequest", BadRequest)
    rejected = FakeMessage(bot=Bot(exc=BadRequest("rejected")))
    assert not await payments._run_provider_refund(rejected, plan())
    assert "Telegram отклонил" in rejected.answers[-1][0]

    already = FakeMessage(bot=Bot(exc=BadRequest("already refunded")))
    assert await payments._run_provider_refund(already, plan())

    class ApiError(Exception):
        pass

    monkeypatch.setattr(payments, "TelegramAPIError", ApiError)
    ambiguous = FakeMessage(bot=Bot(exc=ApiError("api")))
    assert not await payments._run_provider_refund(ambiguous, plan())
    assert "не вернул однозначный" in ambiguous.answers[-1][0]

    monkeypatch.setattr(
        payments,
        "mark_stars_refund_provider_succeeded",
        lambda _charge: (_ for _ in ()).throw(payments.StarsRefundError("db")),
    )
    local_failure = FakeMessage(bot=Bot(True))
    assert not await payments._run_provider_refund(local_failure, plan())
    assert "локальная финализация" in local_failure.answers[-1][0]


@pytest.mark.asyncio
async def test_provider_refund_failure_restore_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    async def restored(*_args: Any, **_kwargs: Any) -> bool:
        return True

    monkeypatch.setattr(payments, "_cancel_refund_hold", restored)
    message = FakeMessage()
    assert not await payments._provider_refund_failure(message, "c", "e", "lead")
    assert "восстановлены" in message.answers[-1][0]

    async def not_restored(*_args: Any, **_kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(payments, "_cancel_refund_hold", not_restored)
    manual = FakeMessage()
    assert not await payments._provider_refund_failure(manual, "c", "e", "lead")
    assert "ручная проверка" in manual.answers[-1][0]

    ambiguous = FakeMessage()
    assert not await payments._provider_refund_ambiguous(ambiguous, "c", RuntimeError("x"))
    assert "Повтор безопасен" in ambiguous.answers[-1][0]


@pytest.mark.asyncio
async def test_refund_command_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payments, "is_platform_admin", lambda uid: uid == 7)
    unauthorized = FakeMessage(8, text="/refundstars c CONFIRM")
    await payments.cmd_refund_stars(unauthorized)
    assert unauthorized.answers == []

    usage = FakeMessage(7, text="/refundstars")
    await payments.cmd_refund_stars(usage)
    assert "Использование" in usage.answers[0][0]

    current = plan()

    async def load(*_args: Any, **_kwargs: Any) -> Any:
        return current

    async def allow(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def prepare(*_args: Any, **_kwargs: Any) -> Any:
        return current

    async def run(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def complete(*_args: Any, **_kwargs: Any) -> Any:
        return plan(status="completed")

    monkeypatch.setattr(payments, "_load_refund_plan", load)
    monkeypatch.setattr(payments, "_refund_plan_allows_execution", allow)
    monkeypatch.setattr(payments, "_prepare_refund_plan", prepare)
    monkeypatch.setattr(payments, "_run_provider_refund", run)
    monkeypatch.setattr(payments, "_complete_refund", complete)
    monkeypatch.setattr(payments, "refund_plan_text", lambda value: f"plan:{value.status}")
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(payments, "log_event", lambda *args: events.append(args))

    message = FakeMessage(7, text="/refundstars charge-1 CONFIRM")
    await payments.cmd_refund_stars(message)
    assert events and events[0][1] == "telegram_stars_refunded"
    assert "Stars возвращены" in message.answers[-1][0]
