from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable, Iterator
from zoneinfo import ZoneInfo

import pytest

from handlers import menu


class FakeUser:
    def __init__(self, user_id: int = 7) -> None:
        self.id = user_id


class FakeMessage:
    def __init__(
        self,
        user_id: int | None = 7,
        *,
        text: str | None = "text",
        caption: str | None = None,
        edit_exc: BaseException | None = None,
    ) -> None:
        self.from_user = FakeUser(user_id) if user_id is not None else None
        self.text = text
        self.caption = caption
        self.edit_exc = edit_exc
        self.answers: list[tuple[str, dict[str, Any]]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append((text, kwargs))
        if self.edit_exc is not None:
            raise self.edit_exc


class FakeCallback:
    def __init__(
        self,
        user_id: int = 7,
        *,
        message: Any | None = None,
        data: str = "menu_main",
        answer_exc: BaseException | None = None,
    ) -> None:
        self.from_user = FakeUser(user_id)
        self.message = message
        self.data = data
        self.answer_exc = answer_exc
        self.answer_calls = 0

    async def answer(self, *_args: Any, **_kwargs: Any) -> None:
        self.answer_calls += 1
        if self.answer_exc is not None:
            raise self.answer_exc


class FakeState:
    def __init__(self, exc: BaseException | None = None) -> None:
        self.exc = exc
        self.clear_calls = 0

    async def clear(self) -> None:
        self.clear_calls += 1
        if self.exc is not None:
            raise self.exc


async def direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


def install_types(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(menu, "Message", FakeMessage)
    monkeypatch.setattr(menu, "CallbackQuery", FakeCallback)


def test_callback_and_user_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    install_types(monkeypatch)
    message = FakeMessage(7)
    callback = FakeCallback(8, message=message)
    assert menu._callback_message(callback) is message
    assert menu._callback_message(FakeCallback(message=object())) is None
    assert menu._callback_user_id(callback) == 8
    assert menu._message_user_id(message) == 7
    assert menu._message_user_id(FakeMessage(None)) is None


def test_log_funnel_safe_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(menu, "log_event", lambda *args: calls.append(args))
    menu._log_funnel_safe(7, "event", {"x": 1})
    assert calls == [(7, "event", {"x": 1})]

    monkeypatch.setattr(
        menu,
        "log_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("analytics")),
    )
    menu._log_funnel_safe(7, "event")


def test_timezone_parse_and_admin_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(menu, "settings", SimpleNamespace(TIMEZONE="UTC", admin_id_list=[1, "2"]))
    assert menu._tz().key == "UTC"
    assert menu._parse_hhmm("") is None
    assert menu._parse_hhmm("8") is None
    assert menu._parse_hhmm("aa:10") is None
    assert menu._parse_hhmm("24:00") is None
    assert menu._parse_hhmm("23:60") is None
    assert menu._parse_hhmm("08:05") == (8, 5)
    assert menu._is_admin(1) is True
    assert menu._is_admin(3) is False

    monkeypatch.setattr(menu, "settings", SimpleNamespace(admin_id_list=object()))
    assert menu._is_admin(1) is False


@contextmanager
def fake_db(row: Any) -> Iterator[Any]:
    class Conn:
        def execute(self, _sql: str, _params: tuple[Any, ...]) -> Any:
            return SimpleNamespace(fetchone=lambda: row)

    yield Conn()


def test_load_work_time_row(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {"work_time": "09:15"}
    monkeypatch.setattr(menu, "db", lambda: fake_db(row))
    assert menu._load_work_time_row(7) == row


@pytest.mark.asyncio
async def test_safe_edit_success_and_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadRequest(Exception):
        pass

    monkeypatch.setattr(menu, "TelegramBadRequest", BadRequest)

    media = FakeMessage(text=None, caption=None)
    await menu.safe_edit(media, "new", reply_markup="kb", parse_mode="HTML")
    assert media.answers == [("new", {"reply_markup": "kb", "parse_mode": "HTML"})]

    caption = FakeMessage(text=None, caption="caption")
    await menu.safe_edit(caption, "edited")
    assert caption.edits[0][0] == "edited"

    unchanged = FakeMessage(edit_exc=BadRequest("message is not modified"))
    await menu.safe_edit(unchanged, "same")
    assert unchanged.answers == []

    no_text = FakeMessage(edit_exc=BadRequest("there is no text in the message to edit"))
    await menu.safe_edit(no_text, "replacement", reply_markup="kb")
    assert no_text.answers[-1] == ("replacement", {"reply_markup": "kb", "parse_mode": None})

    broken = FakeMessage(edit_exc=BadRequest("other telegram failure"))
    with pytest.raises(BadRequest, match="other telegram failure"):
        await menu.safe_edit(broken, "replacement")


@pytest.mark.asyncio
async def test_send_main_menu_message_and_missing_user(monkeypatch: pytest.MonkeyPatch) -> None:
    install_types(monkeypatch)
    monkeypatch.setattr(menu.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(menu, "get_preface", lambda uid, surface: f"preface:{uid}:{surface}\n")
    monkeypatch.setattr(menu, "kb_main", lambda user_id: ("main", user_id))
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(menu, "_log_funnel_safe", lambda *args: events.append(args))

    missing = FakeMessage(None)
    await menu.send_main_menu(missing)
    assert missing.answers == []

    message = FakeMessage(7)
    await menu.send_main_menu(message)
    assert "preface:7:menu" in message.answers[0][0]
    assert message.answers[0][1]["reply_markup"] == ("main", 7)
    assert events[-1][1] == "funnel_main_menu_opened"


@pytest.mark.asyncio
async def test_send_main_menu_callback_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    install_types(monkeypatch)
    monkeypatch.setattr(menu.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(menu, "get_preface", lambda *_args: "")
    monkeypatch.setattr(menu, "kb_main", lambda user_id: ("main", user_id))
    monkeypatch.setattr(menu, "_log_funnel_safe", lambda *_args: None)
    edits: list[tuple[Any, ...]] = []

    async def edit(message: Any, text: str, **kwargs: Any) -> None:
        edits.append((message, text, kwargs))

    monkeypatch.setattr(menu, "safe_edit", edit)

    callback = FakeCallback(7, message=FakeMessage(7))
    await menu.send_main_menu(callback)
    assert callback.answer_calls == 1
    assert edits[-1][2]["reply_markup"] == ("main", 7)

    without_message = FakeCallback(7, message=object())
    await menu.send_main_menu(without_message)
    assert without_message.answer_calls == 1

    class ApiError(Exception):
        pass

    monkeypatch.setattr(menu, "TelegramAPIError", ApiError)
    monkeypatch.setattr(menu, "TelegramBadRequest", ApiError)
    failed_answer = FakeCallback(7, message=FakeMessage(7), answer_exc=ApiError("callback"))
    await menu.send_main_menu(failed_answer)
    assert edits[-1][0] is failed_answer.message


@pytest.mark.asyncio
@pytest.mark.parametrize("handler_name", ["cb_menu_main", "cb_menu_main_v2", "cb_back_main"])
async def test_menu_callbacks_clear_state_and_reopen(
    monkeypatch: pytest.MonkeyPatch,
    handler_name: str,
) -> None:
    callback = FakeCallback()
    answered: list[Any] = []
    reopened: list[Any] = []

    async def safe_answer(value: Any) -> None:
        answered.append(value)

    async def reopen(value: Any) -> None:
        reopened.append(value)

    monkeypatch.setattr(menu, "safe_answer_callback", safe_answer)
    monkeypatch.setattr(menu, "send_main_menu", reopen)
    handler = getattr(menu, handler_name)

    state = FakeState()
    await handler(callback, state)
    assert state.clear_calls == 1
    assert answered == [callback]
    assert reopened == [callback]

    failed = FakeState(RuntimeError("state"))
    await handler(callback, failed)
    await handler(callback, None)
    assert failed.clear_calls == 1


@pytest.mark.asyncio
async def test_demo_menu_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    install_types(monkeypatch)
    monkeypatch.setattr(menu.asyncio, "to_thread", direct_to_thread)
    answered: list[Any] = []

    async def safe_answer(value: Any) -> None:
        answered.append(value)

    monkeypatch.setattr(menu, "safe_answer_callback", safe_answer)
    missing = FakeCallback(message=object(), data="demo")
    await menu.cb_demo_menu(missing)
    assert answered == [missing]

    stages: list[tuple[Any, ...]] = []
    events: list[tuple[Any, ...]] = []
    edits: list[tuple[Any, ...]] = []
    monkeypatch.setattr(menu, "set_funnel_stage", lambda *args: stages.append(args))
    monkeypatch.setattr(menu, "get_preface", lambda uid, surface: f"p:{uid}:{surface}\n")
    monkeypatch.setattr(menu, "_log_funnel_safe", lambda *args: events.append(args))
    monkeypatch.setattr(menu, "kb_demo_kind", lambda: "demo-kb")

    async def edit(message: Any, text: str, **kwargs: Any) -> None:
        edits.append((message, text, kwargs))

    monkeypatch.setattr(menu, "safe_edit", edit)
    callback = FakeCallback(7, message=FakeMessage(7), data="demo")
    await menu.cb_demo_menu(callback)
    assert stages == [(7, "d0")]
    assert events[-1][1] == "funnel_demo_clicked"
    assert "Бесплатная практика" in edits[-1][1]
    assert edits[-1][2]["reply_markup"] == "demo-kb"


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz: ZoneInfo | None = None) -> FixedDateTime:
        base = cls(2026, 7, 21, 9, 0, 0, tzinfo=ZoneInfo("UTC"))
        return base.astimezone(tz) if tz is not None else base


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "morning", "expected_hhmm"),
    [
        ({"work_time": "09:15"}, "08:30", "09:15"),
        ({"work_time": "bad"}, "07:40", "07:40"),
        (None, "invalid", "08:30"),
    ],
)
async def test_continue_tomorrow_schedules_deterministically(
    monkeypatch: pytest.MonkeyPatch,
    row: Any,
    morning: str,
    expected_hhmm: str,
) -> None:
    install_types(monkeypatch)
    monkeypatch.setattr(menu.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(menu, "datetime", FixedDateTime)
    monkeypatch.setattr(menu, "_tz", lambda: ZoneInfo("UTC"))
    monkeypatch.setattr(
        menu,
        "settings",
        SimpleNamespace(MORNING_TIME=morning, TIMEZONE="UTC", admin_id_list=[]),
    )
    monkeypatch.setattr(menu, "_load_work_time_row", lambda _uid: row)
    monkeypatch.setattr(menu, "kb_back_main", lambda: "back-kb")
    cancelled: list[tuple[Any, ...]] = []
    jobs: list[tuple[Any, ...]] = []
    monkeypatch.setattr(menu, "cancel_jobs", lambda *args, **kwargs: cancelled.append((args, kwargs)))
    monkeypatch.setattr(menu, "add_job", lambda *args, **kwargs: jobs.append((args, kwargs)))

    async def safe_answer(_cb: Any) -> None:
        return None

    monkeypatch.setattr(menu, "safe_answer_callback", safe_answer)
    callback = FakeCallback(7, message=FakeMessage(7), data="remind:continue_tomorrow")
    await menu.cb_remind_continue_tomorrow(callback)

    assert cancelled == [((7,), {"prefix": "remind_"})]
    args, _kwargs = jobs[0]
    assert args[0:2] == (7, "remind_continue")
    assert args[4 if len(args) > 4 else 3]["hhmm"] == expected_hhmm
    assert expected_hhmm in callback.message.answers[-1][0]
    assert callback.message.answers[-1][1]["reply_markup"] == "back-kb"


@pytest.mark.asyncio
async def test_continue_tomorrow_without_message_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    install_types(monkeypatch)

    async def safe_answer(_cb: Any) -> None:
        return None

    monkeypatch.setattr(menu, "safe_answer_callback", safe_answer)
    callback = FakeCallback(message=object(), data="remind:continue_tomorrow")
    await menu.cb_remind_continue_tomorrow(callback)
