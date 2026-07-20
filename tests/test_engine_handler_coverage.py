from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from core import engine as engine_module


class Bot:
    def __init__(self) -> None:
        self.messages: list[tuple[int, str, Any]] = []

    async def send_message(self, user_id: int, text: str, reply_markup: Any = None) -> None:
        self.messages.append((user_id, text, reply_markup))


@pytest.mark.asyncio
async def test_demo_handlers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    engine = engine_module.Engine()
    events: list[Any] = []
    monkeypatch.setattr(engine_module, "log_event", lambda *args: events.append(args))
    bot = Bot()

    await engine._demo_reminder(bot, 1, {"kind": "bad"})
    assert bot.messages and events[-1][1] == "demo_reminder_sent"

    monkeypatch.setattr(engine_module, "_demo_send_state_sync", lambda uid: ({"work", "home"}, False))
    await engine._demo_send(bot, 1, {"kind": "work"})
    assert "оба" in bot.messages[-1][1]

    monkeypatch.setattr(engine_module, "_demo_send_state_sync", lambda uid: ({"work"}, False))
    await engine._demo_send(bot, 1, {"kind": "work"})
    assert "ранее" in bot.messages[-1][1]

    monkeypatch.setattr(engine_module, "_demo_send_state_sync", lambda uid: (set(), False))
    monkeypatch.setattr(engine_module, "pick_demo_file", lambda kind: None)
    await engine._demo_send(bot, 1, {"kind": "work"})
    assert "не найден" in bot.messages[-1][1]

    audio = tmp_path / "demo.mp3"
    audio.write_bytes(b"x")
    monkeypatch.setattr(engine_module, "pick_demo_file", lambda kind: audio)
    monkeypatch.setattr(engine_module, "today_tz", lambda: date(2026, 7, 20))
    monkeypatch.setattr(engine_module, "_create_demo_session_sync", lambda *args, **kwargs: 9)
    monkeypatch.setattr(engine_module, "kb_mood_scale", lambda sid, stage: (sid, stage))
    await engine._demo_send(bot, 1, {"kind": "home"})
    assert bot.messages[-1][2] == (9, "pre")


@pytest.mark.asyncio
async def test_sales_handlers_send_and_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = engine_module.Engine()
    bot = Bot()
    events: list[Any] = []
    monkeypatch.setattr(engine_module, "log_event", lambda *args: events.append(args))
    monkeypatch.setattr(engine_module, "funnel_text", lambda step, **kwargs: f"{step}:{kwargs}")
    monkeypatch.setattr(engine_module, "funnel_text_ab", lambda step, ab, **kwargs: f"{step}:{ab}")
    monkeypatch.setattr(engine_module, "_hour_context_sync", lambda uid: 12)
    monkeypatch.setattr(engine_module, "_is_sub_active_sync", lambda uid: False)
    monkeypatch.setattr(engine_module, "_demo_ack_exists_sync", lambda **kwargs: False)
    monkeypatch.setattr(engine_module, "_has_viewed_tariffs_since_sync", lambda **kwargs: False)

    assert await engine._should_skip_sales(1) is False
    await engine._funnel_nudge(bot, 2, {"kind": "work", "variant": "nextday"})
    assert "завтра" in bot.messages[-1][1]
    await engine._funnel_nudge(bot, 2, {"kind": "home"})
    assert bot.messages[-1][1].startswith("nudge:")
    await engine._funnel_postdemo(bot, 2, {"kind": "work", "ack_at_utc": "now"})
    assert bot.messages[-1][1].startswith("postdemo:")
    await engine._funnel_offer(bot, 2, {"kind": "work"})
    assert bot.messages[-1][1] == "offer:A"
    await engine._funnel_offer(bot, 2, {"kind": "work", "variant": "nextday"})
    assert bot.messages[-1][1] == "offer_nextday:B"
    await engine._funnel_deadline(bot, 2, {"kind": "home"})
    await engine._funnel_lastcall(bot, 2, {"kind": "home"})
    await engine._remind_continue(bot, 2, {"src": "job"})

    monkeypatch.setattr(engine_module, "_demo_ack_exists_sync", lambda **kwargs: True)
    before = len(bot.messages)
    await engine._funnel_nudge(bot, 2, {"kind": "work", "message_id": 10})
    assert len(bot.messages) == before

    monkeypatch.setattr(engine_module, "_has_viewed_tariffs_since_sync", lambda **kwargs: True)
    await engine._funnel_postdemo(bot, 2, {"ack_at_utc": "now"})
    assert len(bot.messages) == before

    monkeypatch.setattr(engine_module, "_is_sub_active_sync", lambda uid: True)
    assert await engine._should_skip_sales(1) is True
    await engine._funnel_nudge(bot, 2, {})
    await engine._funnel_postdemo(bot, 2, {})
    await engine._funnel_offer(bot, 2, {})
    await engine._funnel_deadline(bot, 2, {})
    await engine._funnel_lastcall(bot, 2, {})
    await engine._remind_continue(bot, 2, {})
    assert len(bot.messages) == before


@pytest.mark.asyncio
async def test_paid_prompt_expiry_and_funnel2(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = engine_module.Engine()
    bot = Bot()
    events: list[Any] = []
    monkeypatch.setattr(engine_module, "log_event", lambda *args: events.append(args))

    monkeypatch.setattr(engine_module, "_paid_setup_already_configured_sync", lambda uid: False)
    await engine._after_paid_setup_ping(bot, 1, {})
    assert "назначьте" in bot.messages[-1][1]
    monkeypatch.setattr(engine_module, "_paid_setup_already_configured_sync", lambda uid: True)
    before = len(bot.messages)
    await engine._after_paid_setup_ping(bot, 1, {})
    assert len(bot.messages) == before

    await engine._post_prompt(bot, 1, {})
    monkeypatch.setattr(engine_module, "_post_prompt_idem_kind_sync", lambda sid: "work")
    monkeypatch.setattr(engine_module, "for_session", lambda sid: f"sid:{sid}")
    monkeypatch.setattr(engine_module, "for_job_run_at", lambda *args: "wall")
    monkeypatch.setattr(engine_module, "was_delivered", lambda *args: False)
    marked: list[Any] = []
    monkeypatch.setattr(engine_module, "mark_delivery_once", lambda *args: marked.append(args) or True)
    monkeypatch.setattr(engine_module, "kb_mood_scale", lambda sid, stage: (sid, stage))
    await engine._post_prompt(bot, 1, {"session_id": "10", "run_at": "bad"})
    assert bot.messages[-1][2] == (10, "post") and marked
    monkeypatch.setattr(engine_module, "was_delivered", lambda *args: True)
    before = len(bot.messages)
    await engine._post_prompt(bot, 1, {"session_id": "10"})
    assert len(bot.messages) == before

    monkeypatch.setattr(engine_module, "_is_sub_active_sync", lambda uid: False)
    await engine._sub_expiring_soon(bot, 1, {"expires_at": "date"})
    assert len(bot.messages) == before
    monkeypatch.setattr(engine_module, "_is_sub_active_sync", lambda uid: True)
    await engine._sub_expiring_soon(bot, 1, {"expires_at": "date"})
    assert "date" in bot.messages[-1][1]

    monkeypatch.setattr(engine_module, "_funnel2_demo_nopay_guard_sync", lambda uid, payload: "skip")
    before = len(bot.messages)
    await engine._funnel2_demo_nopay_24h(bot, 1, {})
    assert len(bot.messages) == before
    monkeypatch.setattr(engine_module, "_funnel2_demo_nopay_guard_sync", lambda uid, payload: "send")
    await engine._funnel2_demo_nopay_24h(bot, 1, {})
    monkeypatch.setattr(engine_module, "_funnel2_expired_return_guard_sync", lambda uid, payload: "send")
    await engine._funnel2_expired_return_3d(bot, 1, {})
    assert len(bot.messages) == before + 2
