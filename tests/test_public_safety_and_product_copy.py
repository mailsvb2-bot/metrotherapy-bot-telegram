from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable

import pytest

from handlers import start
from services import funnel_texts
from services.payments import gift


class FakeMessage:
    def __init__(self, user_id: int = 7) -> None:
        self.from_user = SimpleNamespace(id=user_id, full_name="User")
        self.answers: list[tuple[str, dict[str, Any]]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append((text, kwargs))


class FakeCallback:
    def __init__(self, message: Any, user_id: int = 7) -> None:
        self.message = message
        self.from_user = SimpleNamespace(id=user_id, full_name="User")


async def direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


def test_help_copy_contains_explicit_safety_stop_rules() -> None:
    text = start.HELP_TEXT
    assert "Не запускайте практику за рулём" in text
    assert "Дождитесь безопасной остановки" in text
    assert "остановите аудио" in text
    assert "остром или небезопасном состоянии" in text


@pytest.mark.asyncio
async def test_tariffs_command_describes_practice_packages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(start.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(start, "_log_safe", lambda *_args, **_kwargs: None)
    message = FakeMessage()

    await start.tariffs_cmd(message)

    text, kwargs = message.answers[-1]
    assert text.startswith("💳 Пакеты практик")
    assert "подписк" not in text.casefold()
    button = kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "💳 Открыть пакеты практик"
    assert button.callback_data == "sub:menu"


def test_default_funnel_copy_uses_package_model_and_single_override_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def no_custom(step: str, variant: str) -> None:
        calls.append((step, variant))
        return None

    monkeypatch.setattr(funnel_texts, "get_active_copy", no_custom)

    for step in ("nudge", "postdemo", "deadline", "lastcall"):
        text = funnel_texts.funnel_text(step)  # type: ignore[arg-type]
        assert "подписк" not in text.casefold()
        assert "пакет" in text.casefold()

    calls.clear()
    text = funnel_texts.funnel_text_ab("offer", "A")
    assert calls == [("offer", "A")]
    assert "подписк" not in text.casefold()
    assert "пакет практик" in text.casefold()

    calls.clear()
    text = funnel_texts.funnel_text_ab("offer_nextday", "B")
    assert calls == [("offer_nextday", "B")]
    assert "подписк" not in text.casefold()
    assert "пакет практик" in text.casefold()


@pytest.mark.asyncio
async def test_gift_menu_explains_one_time_package_purchase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gift, "Message", FakeMessage)
    monkeypatch.setattr(gift, "set_pending", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gift, "log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(gift, "kb_gift_tariffs", lambda **kwargs: ("packages", kwargs))
    message = FakeMessage()

    await gift.gift_menu(FakeCallback(message))

    text, kwargs = message.edits[-1]
    assert "пакет практик" in text.casefold()
    assert "разовая покупка" in text.casefold()
    assert "не автопродляемая подписка" in text.casefold()
    assert kwargs["reply_markup"][0] == "packages"
