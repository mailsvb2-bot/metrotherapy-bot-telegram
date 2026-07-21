from __future__ import annotations

import builtins
from typing import Any, Callable

import pytest

from handlers import start
from tests.test_start_onboarding_phase6 import FakeMessage


async def direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


@pytest.mark.asyncio
async def test_legacy_gift_import_failure_answers_and_opens_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(start.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(start, "normalize_gift_token", lambda payload: payload)
    monkeypatch.setattr(start, "is_gift_token", lambda _token: False)
    monkeypatch.setattr(start, "_register_user_entry_safe", lambda *_args: None)
    opened: list[Any] = []

    async def open_menu(message: Any, **_kwargs: Any) -> None:
        opened.append(message)

    monkeypatch.setattr(start, "_open_main_menu_fail_open", open_menu)
    original_import = builtins.__import__

    def import_hook(name: str, *args: Any, **kwargs: Any):
        if name == "handlers.gift_flow":
            raise ImportError("gift flow unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_hook)
    message = FakeMessage(7, text="/start gift_import")
    await start.start_cmd(message)

    assert "Откройте ссылку ещё раз" in message.answers[0][0]
    assert opened == [message]
