from __future__ import annotations

import builtins
import sqlite3
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from handlers import start


class FakeUser:
    def __init__(self, user_id: int = 7) -> None:
        self.id = user_id
        self.username = "user"
        self.full_name = "User Name"
        self.first_name = "User"


class FakeMessage:
    def __init__(
        self,
        user_id: int | None = 7,
        *,
        text: str | None = "/start",
        answer_exc: BaseException | None = None,
    ) -> None:
        self.from_user = FakeUser(user_id) if user_id is not None else None
        self.text = text
        self.answer_exc = answer_exc
        self.answers: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))
        if self.answer_exc:
            raise self.answer_exc


async def direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


def claim_result(**kwargs: Any) -> SimpleNamespace:
    values = {"status": "claimed", "package_id": "pkg", "message": "gift claimed"}
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_start_user_helpers() -> None:
    message = FakeMessage(7)
    assert start._message_user(message) is message.from_user
    assert start._user_id(message) == 7
    assert start._message_user(FakeMessage(None)) is None
    assert start._user_id(FakeMessage(None)) is None


def test_log_safe_success_skip_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(start, "log_event", lambda *args: events.append(args))
    start._log_safe(None, "event")
    assert events == []

    start._log_safe(7, "event", {"a": 1})
    assert events == [(7, "event", {"a": 1})]

    monkeypatch.setattr(
        start,
        "log_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("db")),
    )
    start._log_safe(7, "event")


def test_register_user_entry_safe_all_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(start, "register_user_entry", lambda *args, **kwargs: calls.append((args, kwargs)))
    start._register_user_entry_safe(FakeMessage(None), "payload")
    assert calls == []

    message = FakeMessage(7)
    start._register_user_entry_safe(message, "payload")
    args, kwargs = calls[0]
    assert args[0] == 7
    assert kwargs == {
        "platform": "telegram",
        "external_user_id": "7",
        "username": "user",
        "display_name": "User Name",
        "first_name": "User",
        "start_payload": "payload",
    }

    monkeypatch.setattr(
        start,
        "register_user_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad payload")),
    )
    start._register_user_entry_safe(message, "bad")

    monkeypatch.setattr(
        start,
        "register_user_entry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db")),
    )
    start._register_user_entry_safe(message, "bad")


def test_claim_gift_safe_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "личного профиля" in start._claim_gift_safe(FakeMessage(None), "gift_token")

    registrations: list[tuple[Any, ...]] = []
    monkeypatch.setattr(start, "_register_user_entry_safe", lambda *args: registrations.append(args))
    monkeypatch.setattr(start, "claim_gift_token", lambda **_kwargs: claim_result())
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(start, "log_event", lambda *args: events.append(args))

    message = FakeMessage(7)
    assert start._claim_gift_safe(message, "gift_token") == "gift claimed"
    assert registrations == [(message, "gift_token")]
    assert events[-1][1] == "gift_claim_attempt"

    monkeypatch.setattr(
        start,
        "log_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(sqlite3.OperationalError("db")),
    )
    assert start._claim_gift_safe(message, "gift_token") == "gift claimed"


@pytest.mark.asyncio
async def test_open_main_menu_success_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    async def menu(message: Any) -> None:
        calls.append(message)

    monkeypatch.setattr(start, "send_main_menu", menu)
    message = FakeMessage(7)
    await start._open_main_menu_fail_open(message)
    assert calls == [message]
    assert message.answers == []

    async def menu_failure(_message: Any) -> None:
        raise RuntimeError("menu failed")

    monkeypatch.setattr(start, "send_main_menu", menu_failure)
    monkeypatch.setattr(start, "kb_main", lambda user_id: ("main", user_id))
    fallback = FakeMessage(7)
    await start._open_main_menu_fail_open(fallback, fallback_text="fallback")
    assert fallback.answers == [("fallback", {"reply_markup": ("main", 7)})]

    class ApiError(Exception):
        pass

    monkeypatch.setattr(start, "TelegramAPIError", ApiError)
    broken = FakeMessage(7, answer_exc=ApiError("answer failed"))
    with pytest.raises(ApiError, match="answer failed"):
        await start._open_main_menu_fail_open(broken)


@pytest.mark.asyncio
async def test_start_cmd_canonical_gift_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(start.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(start, "normalize_gift_token", lambda payload: payload.strip())
    monkeypatch.setattr(start, "is_gift_token", lambda token: token == "gift-token")
    monkeypatch.setattr(start, "_claim_gift_safe", lambda *_args: "claimed")
    monkeypatch.setattr(start, "kb_main", lambda user_id: ("main", user_id))
    opened: list[Any] = []

    async def open_menu(message: Any, **_kwargs: Any) -> None:
        opened.append(message)

    monkeypatch.setattr(start, "_open_main_menu_fail_open", open_menu)
    message = FakeMessage(7, text="/start gift-token")
    await start.start_cmd(message)
    assert message.answers == [("claimed", {"reply_markup": ("main", 7)})]
    assert opened == [message]


@pytest.mark.asyncio
async def test_start_cmd_legacy_gift_success_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(start.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(start, "normalize_gift_token", lambda payload: payload)
    monkeypatch.setattr(start, "is_gift_token", lambda _token: False)
    registrations: list[tuple[Any, ...]] = []
    monkeypatch.setattr(start, "_register_user_entry_safe", lambda *args: registrations.append(args))
    opened: list[Any] = []

    async def open_menu(message: Any, **_kwargs: Any) -> None:
        opened.append(message)

    monkeypatch.setattr(start, "_open_main_menu_fail_open", open_menu)

    from handlers import gift_flow

    sent: list[tuple[Any, str]] = []

    async def intro(message: Any, code: str) -> None:
        sent.append((message, code))

    monkeypatch.setattr(gift_flow, "send_gift_intro", intro)
    success = FakeMessage(7, text="/start gift_abc")
    await start.start_cmd(success)
    assert registrations[-1] == (success, "gift_abc")
    assert sent == [(success, "abc")]
    assert opened[-1] is success

    async def db_error(_message: Any, _code: str) -> None:
        raise sqlite3.OperationalError("db")

    monkeypatch.setattr(gift_flow, "send_gift_intro", db_error)
    failed = FakeMessage(7, text="/start gift_db")
    await start.start_cmd(failed)
    assert "Откройте ссылку ещё раз" in failed.answers[-1][0]

    async def value_error(_message: Any, _code: str) -> None:
        raise ValueError("bad")

    monkeypatch.setattr(gift_flow, "send_gift_intro", value_error)
    unexpected = FakeMessage(7, text="/start gift_bad")
    await start.start_cmd(unexpected)
    assert "Откройте ссылку ещё раз" in unexpected.answers[-1][0]


def test_import_hook_for_legacy_gift_error(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def import_hook(name: str, *args: Any, **kwargs: Any):
        if name == "handlers.gift_flow":
            raise ImportError("missing gift flow")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_hook)
    with pytest.raises(ImportError):
        __import__("handlers.gift_flow")


@pytest.mark.asyncio
async def test_start_cmd_plain_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(start.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(start, "normalize_gift_token", lambda payload: payload)
    monkeypatch.setattr(start, "is_gift_token", lambda _token: False)
    order: list[tuple[str, Any]] = []

    async def open_menu(message: Any, **_kwargs: Any) -> None:
        order.append(("menu", message))

    monkeypatch.setattr(start, "_open_main_menu_fail_open", open_menu)
    monkeypatch.setattr(start, "_register_user_entry_safe", lambda message, payload: order.append(("register", payload)))
    monkeypatch.setattr(start, "start_attribution_meta", lambda payload: {"payload": payload})
    monkeypatch.setattr(start, "_log_safe", lambda uid, event, data: order.append((event, (uid, data))))

    message = FakeMessage(7, text="/start campaign")
    await start.start_cmd(message)
    assert order[0][0] == "menu"
    assert ("register", "campaign") in order
    assert ("funnel_start_command", (7, {"payload": "campaign"})) in order

    no_payload = FakeMessage(7, text="/start")
    await start.start_cmd(no_payload)
    assert ("register", "") in order


@pytest.mark.asyncio
async def test_claim_gift_text_and_start_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(start.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(start, "normalize_gift_token", lambda text: text.strip())
    monkeypatch.setattr(start, "_claim_gift_safe", lambda *_args: "claimed text")
    monkeypatch.setattr(start, "kb_main", lambda user_id: ("main", user_id))
    message = FakeMessage(7, text=" gift-token ")
    await start.claim_gift_text(message)
    assert message.answers == [("claimed text", {"reply_markup": ("main", 7)})]

    opened: list[tuple[Any, str]] = []

    async def open_menu(message: Any, *, fallback_text: str) -> None:
        opened.append((message, fallback_text))

    monkeypatch.setattr(start, "_open_main_menu_fail_open", open_menu)
    fallback = FakeMessage(7, text="menu")
    await start.safe_start_text_fallback(fallback)
    assert "Главное меню" in opened[0][1]


@pytest.mark.asyncio
async def test_public_command_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(start.asyncio, "to_thread", direct_to_thread)
    events: list[tuple[Any, ...]] = []
    monkeypatch.setattr(start, "_log_safe", lambda *args: events.append(args))
    monkeypatch.setattr(start, "kb_demo_kind", lambda: "demo-kb")
    monkeypatch.setattr(start, "kb_main", lambda user_id: ("main", user_id))

    programs = FakeMessage(7)
    await start.programs_cmd(programs)
    assert programs.answers[0][1]["reply_markup"] == "demo-kb"

    tariffs = FakeMessage(7)
    await start.tariffs_cmd(tariffs)
    tariff_buttons = tariffs.answers[0][1]["reply_markup"].inline_keyboard
    assert tariff_buttons[0][0].callback_data == "sub:menu"
    assert tariff_buttons[1][0].callback_data == "demo"

    progress = FakeMessage(7)
    await start.progress_cmd(progress)
    progress_buttons = progress.answers[0][1]["reply_markup"].inline_keyboard
    assert progress_buttons[0][0].callback_data == "settings:state"
    assert progress_buttons[1][0].callback_data == "menu:main"

    help_message = FakeMessage(7)
    await start.help_cmd(help_message)
    assert help_message.answers == [(start.HELP_TEXT, {"reply_markup": ("main", 7)})]

    site = FakeMessage(7)
    await start.site_cmd(site)
    assert site.answers == [(start.SITE_TEXT, {})]

    privacy = FakeMessage(7)
    await start.privacy_cmd(privacy)
    assert privacy.answers == [(start.PRIVACY_TEXT, {})]

    names = [event for _uid, event, _meta in events]
    assert names == [
        "funnel_programs_command",
        "funnel_tariffs_command",
        "funnel_progress_command",
        "funnel_help_command",
        "funnel_site_command",
        "funnel_privacy_command",
    ]
