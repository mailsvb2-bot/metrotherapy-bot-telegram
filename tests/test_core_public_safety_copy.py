from __future__ import annotations

import inspect
from typing import Any, Callable

import pytest

from core import engine as engine_module


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, dict[str, Any]]] = []

    async def send_message(self, user_id: int, text: str, **kwargs: Any) -> None:
        self.messages.append((user_id, text, kwargs))


async def direct_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return func(*args, **kwargs)


def _button_pairs(markup: Any) -> list[tuple[str, str]]:
    return [
        (button.text, button.callback_data)
        for row in markup.inline_keyboard
        for button in row
    ]


@pytest.mark.asyncio
async def test_demo_reminder_forbids_driving_and_allows_safe_passenger_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine_module.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(engine_module, "log_event", lambda *_args, **_kwargs: None)
    bot = FakeBot()

    await engine_module.engine._demo_reminder(bot, 7, {"kind": "work"})

    assert len(bot.messages) == 1
    _user_id, text, _kwargs = bot.messages[0]
    assert "Не включайте практику за рулём" in text
    assert "Дождитесь безопасной остановки" in text
    assert "слушайте как пассажир" in text
    assert "Если за рулём — просто включите" not in text


def test_core_cta_buttons_use_practice_package_model_without_callback_breakage() -> None:
    markups = (
        engine_module.engine._kb_after_demo("work", allow_other=False),
        engine_module.engine._kb_funnel(7),
        engine_module.engine._kb_offer(7),
    )
    pairs = [pair for markup in markups for pair in _button_pairs(markup)]

    subscription_ctas = [text for text, callback in pairs if callback == "sub:menu"]
    assert subscription_ctas
    assert set(subscription_ctas) == {"💳 Пакеты практик"}
    assert ("🎁 Подарить пакет практик", "gift:menu") in pairs
    assert all(text != "💳 Подписка" for text, _callback in pairs)


def test_core_public_copy_no_longer_contains_known_unsafe_or_stale_phrases() -> None:
    source = inspect.getsource(engine_module)

    forbidden = (
        "Если за рулём — просто включите и слушайте безопасно.",
        "пожалуйста, оформите подписку.",
        "или оформить подписку.",
        "можно выбрать подписку.",
        "откройте подписку и выберите удобный тариф.",
        'text="🎁 Подарить подписку другу"',
    )
    for phrase in forbidden:
        assert phrase not in source

    required = (
        "выберите пакет практик",
        "откройте полный маршрут",
        'text="💳 Пакеты практик"',
        'text="🎁 Подарить пакет практик"',
    )
    lowered = source.casefold()
    for phrase in required:
        assert phrase.casefold() in lowered
