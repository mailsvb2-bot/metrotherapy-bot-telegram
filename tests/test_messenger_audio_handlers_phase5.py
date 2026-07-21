from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from handlers import messenger_audio as handlers
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery


class FakeMessage:
    def __init__(self, user_id: int | None = 7, *, bot: Any = None) -> None:
        self.from_user = SimpleNamespace(id=user_id) if user_id is not None else None
        self.bot = bot
        self.answers: list[tuple[str, dict[str, Any]]] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))


def journey(**kwargs: Any) -> SimpleNamespace:
    values = {
        "ready_for_pre_score": False,
        "status": "pending_audio",
        "message": "continue",
        "session_id": 12,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def progress(**kwargs: Any) -> SimpleNamespace:
    values = {
        "pending_item": None,
        "pending_platform": None,
        "last_anchor": None,
        "last_title": "",
        "last_platform": None,
        "next_item": None,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_message_user_id_and_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    assert handlers._message_user_id(FakeMessage(7)) == 7
    assert handlers._message_user_id(FakeMessage(None)) is None

    monkeypatch.setattr(handlers, "TelegramBotSender", lambda bot: ("tg", bot))
    monkeypatch.setattr(handlers, "MaxBotSender", lambda: "max")
    monkeypatch.setattr(handlers, "VkBotSender", lambda: "vk")
    registry = handlers._registry("bot")
    assert registry.telegram == ("tg", "bot")
    assert registry.max == "max"
    assert registry.vk == "vk"


@pytest.mark.asyncio
async def test_continue_audio_guards_and_journey_states(monkeypatch: pytest.MonkeyPatch) -> None:
    missing_user = FakeMessage(None, bot=object())
    await handlers.continue_audio(missing_user)
    assert missing_user.answers == []

    missing_bot = FakeMessage(7, bot=None)
    await handlers.continue_audio(missing_bot)
    assert missing_bot.answers == []

    monkeypatch.setattr(handlers, "kb_mood_scale", lambda session_id, stage: (session_id, stage))
    monkeypatch.setattr(handlers, "start_or_resume_paid_practice", lambda _uid: journey(ready_for_pre_score=True, message="score"))
    score = FakeMessage(7, bot=object())
    await handlers.continue_audio(score)
    assert score.answers == [("score", {"reply_markup": (12, "pre")})]

    monkeypatch.setattr(handlers, "kb_tariffs", lambda uid: ("tariffs", uid))
    monkeypatch.setattr(
        handlers,
        "start_or_resume_paid_practice",
        lambda _uid: journey(status="insufficient_balance", message="pay"),
    )
    insufficient = FakeMessage(7, bot=object())
    await handlers.continue_audio(insufficient)
    assert insufficient.answers == [("pay", {"reply_markup": ("tariffs", 7)})]

    monkeypatch.setattr(
        handlers,
        "start_or_resume_paid_practice",
        lambda _uid: journey(status="completed", message="done"),
    )
    completed = FakeMessage(7, bot=object())
    await handlers.continue_audio(completed)
    assert completed.answers == [("done", {})]


@pytest.mark.asyncio
async def test_continue_audio_delivery_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = object()
    monkeypatch.setattr(handlers, "start_or_resume_paid_practice", lambda _uid: journey())
    monkeypatch.setattr(handlers, "_registry", lambda actual_bot: SenderRegistry(telegram=actual_bot))
    calls: list[dict[str, Any]] = []

    async def send_ok(uid: int, **kwargs: Any) -> SimpleNamespace:
        calls.append({"uid": uid, **kwargs})
        return SimpleNamespace(message="audio sent")

    monkeypatch.setattr(handlers, "send_next_audio_to_user", send_ok)
    message = FakeMessage(7, bot=bot)
    await handlers.continue_audio(message)
    assert message.answers == [("audio sent", {})]
    assert calls[0]["target_platform"] == "telegram"
    assert calls[0]["fallback"] == "telegram"
    assert calls[0]["telegram_bot"] is bot

    async def send_failed(*_args: Any, **_kwargs: Any) -> None:
        raise UnsupportedMessengerDelivery("no transport")

    monkeypatch.setattr(handlers, "send_next_audio_to_user", send_failed)
    failed = FakeMessage(7, bot=bot)
    await handlers.continue_audio(failed)
    assert "Не удалось отправить" in failed.answers[0][0]


@pytest.mark.asyncio
async def test_confirm_audio_without_pending_or_session(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = FakeMessage(None)
    await handlers.confirm_audio(missing)
    assert missing.answers == []

    monkeypatch.setattr(handlers, "find_pending_post_session_id", lambda _uid: None)
    monkeypatch.setattr(handlers, "get_session", lambda _sid: None)
    monkeypatch.setattr(handlers, "confirm_pending_audio_delivery", lambda *_args, **_kwargs: None)
    empty = FakeMessage(7)
    await handlers.confirm_audio(empty)
    assert "нет аудио" in empty.answers[0][0]

    confirmed = SimpleNamespace(anchor=3, title="Track")
    monkeypatch.setattr(handlers, "confirm_pending_audio_delivery", lambda *_args, **_kwargs: confirmed)
    direct = FakeMessage(7)
    await handlers.confirm_audio(direct)
    assert "№3" in direct.answers[0][0]
    assert "/continue" in direct.answers[0][0]


@pytest.mark.asyncio
async def test_confirm_audio_demo_and_paid_post_score(monkeypatch: pytest.MonkeyPatch) -> None:
    confirmed = SimpleNamespace(anchor=4, title="Calm")
    monkeypatch.setattr(handlers, "find_pending_post_session_id", lambda _uid: 22)
    monkeypatch.setattr(handlers, "get_session", lambda _sid: SimpleNamespace(source="demo"))
    seen: list[dict[str, Any]] = []

    def confirm(uid: int, **kwargs: Any) -> Any:
        seen.append({"uid": uid, **kwargs})
        return confirmed

    monkeypatch.setattr(handlers, "confirm_pending_audio_delivery", confirm)
    monkeypatch.setattr(handlers, "kb_mood_scale", lambda session_id, stage: (session_id, stage))
    message = FakeMessage(7)
    await handlers.confirm_audio(message)
    assert seen[0]["sequence_key"] == "demo"
    assert "ПОСЛЕ" in message.answers[0][0]
    assert message.answers[0][1]["reply_markup"] == (22, "post")

    monkeypatch.setattr(handlers, "get_session", lambda _sid: SimpleNamespace(source="paid"))
    monkeypatch.setattr(handlers, "confirm_pending_audio_delivery", lambda *_args, **_kwargs: None)
    paid = FakeMessage(7)
    await handlers.confirm_audio(paid)
    assert "текущее аудио" in paid.answers[0][0]


@pytest.mark.asyncio
async def test_audio_progress_all_states(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = FakeMessage(None)
    await handlers.audio_progress(missing)
    assert missing.answers == []

    monkeypatch.setattr(handlers, "get_progress_snapshot", lambda _uid: progress())
    empty = FakeMessage(7)
    await handlers.audio_progress(empty)
    assert "не найдена" in empty.answers[0][0]

    pending = SimpleNamespace(anchor=1, title="First")
    next_item = SimpleNamespace(anchor=2, title="Second")
    monkeypatch.setattr(handlers, "platform_title", lambda value: f"platform:{value}")
    monkeypatch.setattr(
        handlers,
        "get_progress_snapshot",
        lambda _uid: progress(pending_item=pending, pending_platform="vk", next_item=next_item),
    )
    fresh = FakeMessage(7)
    await handlers.audio_progress(fresh)
    assert "Следующей будет №2" in fresh.answers[0][0]
    assert "ещё не подтверждено" in fresh.answers[0][0]

    monkeypatch.setattr(
        handlers,
        "get_progress_snapshot",
        lambda _uid: progress(last_anchor=2, last_title="Second", last_platform="max", next_item=SimpleNamespace(anchor=3, title="Third")),
    )
    active = FakeMessage(7)
    await handlers.audio_progress(active)
    assert "Последнее подтверждённое аудио: №2" in active.answers[0][0]
    assert "Следующей будет №3" in active.answers[0][0]

    monkeypatch.setattr(
        handlers,
        "get_progress_snapshot",
        lambda _uid: progress(last_anchor=60, last_title="Final", last_platform="telegram"),
    )
    complete = FakeMessage(7)
    await handlers.audio_progress(complete)
    assert "дослушана до конца" in complete.answers[0][0]


@pytest.mark.asyncio
async def test_audio_history_empty_and_formatted(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = FakeMessage(None)
    await handlers.audio_history(missing)
    assert missing.answers == []

    monkeypatch.setattr(handlers, "get_recent_audio_timeline", lambda *_args, **_kwargs: [])
    empty = FakeMessage(7)
    await handlers.audio_history(empty)
    assert "пока пуста" in empty.answers[0][0]

    events = [
        SimpleNamespace(created_at="2026-01-01", event_type="telegram_sent", anchor=2, title="Track", platform="telegram"),
        SimpleNamespace(created_at="2026-01-02", event_type="custom", anchor=None, title="", platform=""),
    ]
    monkeypatch.setattr(handlers, "get_recent_audio_timeline", lambda *_args, **_kwargs: events)
    monkeypatch.setattr(handlers, "platform_title", lambda value: value.upper())
    history = FakeMessage(7)
    await handlers.audio_history(history)
    text = history.answers[0][0]
    assert "аудио отправлено в Telegram" in text
    assert "№2" in text
    assert "custom" in text


@pytest.mark.asyncio
async def test_switch_channel_missing_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    missing = FakeMessage(None)
    await handlers.switch_channel(missing)
    assert missing.answers == []

    monkeypatch.setattr(handlers, "issue_bridge_token", lambda uid: f"token-{uid}")
    monkeypatch.setattr(handlers, "build_switch_targets", lambda _token: [])
    not_configured = FakeMessage(7)
    await handlers.switch_channel(not_configured)
    assert "не настроены" in not_configured.answers[0][0]

    monkeypatch.setattr(
        handlers,
        "build_switch_targets",
        lambda _token: [
            {"title": "MAX", "url": "https://max.example"},
            {"title": "VK", "url": "https://vk.example"},
        ],
    )
    configured = FakeMessage(7)
    await handlers.switch_channel(configured)
    text, kwargs = configured.answers[0]
    assert "MAX: https://max.example" in text
    assert "VK: https://vk.example" in text
    assert kwargs["disable_web_page_preview"] is True
