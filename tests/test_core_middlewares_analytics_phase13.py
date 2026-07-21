from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.types import CallbackQuery, Chat, Message, TelegramObject, User

from core import middlewares as mw


def user(uid: int = 7) -> User:
    return User.model_construct(id=uid, is_bot=False, first_name="User")


def message(text: str | None = "hello", uid: int | None = 7) -> Message:
    return Message.model_construct(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat.model_construct(id=uid or 1, type="private"),
        from_user=user(uid) if uid is not None else None,
        text=text,
    )


def callback(data: str | None = "menu", uid: int | None = 7) -> CallbackQuery:
    return CallbackQuery.model_construct(
        id="cb",
        from_user=user(uid) if uid is not None else None,
        chat_instance="chat",
        data=data,
    )


async def handler_result(_event: TelegramObject, _data: dict[str, Any]) -> str:
    return "ok"


class Clock:
    def __init__(self, *values: float) -> None:
        self.values = list(values)
        self.last = self.values[-1] if self.values else 0.0

    def monotonic(self) -> float:
        if self.values:
            self.last = self.values.pop(0)
        return self.last


@pytest.mark.asyncio
async def test_state_log_message_states(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[tuple[Any, ...]] = []
    monkeypatch.setattr(mw, "_spawn_bg", lambda _data, fn, *args, **kwargs: spawned.append((fn, args, kwargs)))
    monkeypatch.setattr(mw, "classify_messenger_action", lambda value: f"action:{value}")
    middleware = mw.StateLogMiddleware()

    for text, expected, meta in (
        ("/start", "menu", None),
        ("/start token", "menu", None),
        ("/help", "command", {"action": "action:/help"}),
        ("plain", "text", None),
    ):
        spawned.clear()
        assert await middleware(handler_result, message(text), {}) == "ok"
        assert spawned[0][1] == (7, expected, meta)

    spawned.clear()
    assert await middleware(handler_result, message("plain", uid=None), {}) == "ok"
    assert spawned == []


@pytest.mark.asyncio
async def test_state_log_callback_states_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[tuple[Any, ...]] = []
    monkeypatch.setattr(mw, "_spawn_bg", lambda _data, fn, *args, **kwargs: spawned.append((fn, args, kwargs)))
    monkeypatch.setattr(mw, "classify_messenger_action", lambda value: f"action:{value}")
    middleware = mw.StateLogMiddleware()

    for data, expected in (
        ("demo:work", "demo"),
        ("full", "session"),
        ("work", "session"),
        ("home", "session"),
        ("audio:next", "session"),
        ("back:menu", "menu"),
        ("settings", "callback"),
    ):
        spawned.clear()
        assert await middleware(handler_result, callback(data), {}) == "ok"
        assert spawned[0][1][1] == expected
        assert spawned[0][1][2] == {"action": f"action:{data}"}

    spawned.clear()
    await middleware(handler_result, callback("x", uid=None), {})
    assert spawned == []

    monkeypatch.setattr(mw, "classify_messenger_action", lambda _value: (_ for _ in ()).throw(ValueError("bad")))
    assert await middleware(handler_result, callback("bad"), {}) == "ok"


@pytest.mark.asyncio
async def test_interaction_analytics_message_callback_delta_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    spawned: list[tuple[Any, ...]] = []
    monkeypatch.setattr(mw, "_spawn_bg", lambda _data, fn, *args, **kwargs: spawned.append((fn, args, kwargs)))
    monkeypatch.setattr(mw, "classify_messenger_action", lambda value: f"action:{value}")
    middleware = mw.InteractionAnalyticsMiddleware()
    monkeypatch.setattr(mw, "time", Clock(10.0, 10.25, 11.0))

    assert await middleware(handler_result, callback("pay"), {}) == "ok"
    assert spawned[0][1] == (7, "callback", "action:pay", None)
    assert spawned[1][1] == (7, None)

    spawned.clear()
    assert await middleware(handler_result, message("/help"), {}) == "ok"
    assert spawned[0][1] == (7, "command", "action:/help", 250)
    assert spawned[1][1] == (7, 250)

    spawned.clear()
    assert await middleware(handler_result, message("plain"), {}) == "ok"
    assert spawned[0][1] == (7, "message", None, 750)

    middleware._last_cleanup_mono = 0.0
    middleware._last_mono = {uid: 1.0 for uid in range(2101)}
    spawned.clear()
    monkeypatch.setattr(mw, "time", Clock(10000.0))
    await middleware(handler_result, message("new", uid=5000), {})
    assert middleware._last_cleanup_mono == 10000.0
    assert len(middleware._last_mono) == 1


@pytest.mark.asyncio
async def test_interaction_analytics_no_user_and_spawn_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    middleware = mw.InteractionAnalyticsMiddleware()
    spawned: list[tuple[Any, ...]] = []
    monkeypatch.setattr(mw, "_spawn_bg", lambda *_args, **_kwargs: spawned.append(("called",)))
    assert await middleware(handler_result, message("x", uid=None), {}) == "ok"
    assert await middleware(handler_result, callback("x", uid=None), {}) == "ok"
    assert await middleware(handler_result, TelegramObject.model_construct(), {}) == "ok"
    assert spawned == []

    monkeypatch.setattr(mw, "time", Clock(1.0))
    monkeypatch.setattr(mw, "_spawn_bg", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("bad")))
    assert await middleware(handler_result, message("plain"), {}) == "ok"
