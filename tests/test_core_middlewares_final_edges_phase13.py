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


async def handler(_event: TelegramObject, _data: dict[str, Any]) -> str:
    return "ok"


def test_handler_label_raw_callback_and_fallback_key() -> None:
    def direct() -> None:
        return None

    assert "direct" in mw.SlowHandlerLogMiddleware._handler_label({"handler": direct})
    blank_callback = SimpleNamespace(__module__="", __qualname__="", __name__="")
    blank_handler = SimpleNamespace(callback=blank_callback)
    assert "direct" in mw.SlowHandlerLogMiddleware._handler_label(
        {"handler": blank_handler, "event_handler": direct}
    )


def test_wrapped_and_generic_event_details(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mw, "classify_messenger_action", lambda value: f"action:{value}")
    wrapped = SimpleNamespace(update_id=55, message=message("/help"))
    details = mw.SlowHandlerLogMiddleware._event_details(wrapped)
    assert details["inner"] == "Message"
    assert details["update_id"] == 55
    assert details["payload"] == "message_action=action:/help"

    generic = TelegramObject.model_construct()
    generic_details = mw.SlowHandlerLogMiddleware._event_details(generic)
    assert generic_details["inner"] == "TelegramObject"
    assert generic_details["payload"] == "TelegramObject"


@pytest.mark.asyncio
async def test_rate_limiter_events_without_identity_and_disabled_interval() -> None:
    limiter = mw.SoftRateLimitMiddleware(callback_interval_sec=0, message_interval_sec=0)
    assert await limiter(handler, message("x", uid=None), {}) == "ok"
    assert await limiter(handler, callback("x", uid=None), {}) == "ok"
    assert await limiter(handler, TelegramObject.model_construct(), {}) == "ok"
    assert await limiter(handler, message("x", uid=7), {}) == "ok"
    assert await limiter(handler, callback("x", uid=7), {}) == "ok"
    assert limiter._last_ts == {}
