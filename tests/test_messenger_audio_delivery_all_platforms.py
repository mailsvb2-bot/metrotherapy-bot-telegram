from __future__ import annotations

from pathlib import Path
import asyncio
import json

import pytest

from services.db import db
from services.messenger.audio_delivery import send_next_audio_to_user
from services.messenger.audio_progress import AudioProgressItem, get_progress_snapshot
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery
from services.messenger.preferences import record_channel_identity


class _FakeSender:
    def __init__(self, *, fail_audio: bool = False):
        self.fail_audio = fail_audio
        self.audio_calls: list[tuple[str, Path, str | None, dict]] = []
        self.text_calls: list[tuple[str, str, dict]] = []

    async def send_text(self, external_user_id: str, text: str, **kwargs):
        self.text_calls.append((external_user_id, text, kwargs))
        return {"ok": True}

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs):
        self.audio_calls.append((external_user_id, file_path, caption, kwargs))
        if self.fail_audio:
            raise RuntimeError("boom")
        return {"ok": True}


class _FakeTelegramBot:
    def __init__(self):
        self.audio_calls: list[tuple[int, Path, str | None]] = []


async def _fake_send_audio_cached(bot, chat_id: int, *, key: str, file_path: Path, caption: str | None = None, **kwargs):
    bot.audio_calls.append((int(chat_id), file_path, caption))
    return {"ok": True, "key": key}


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(table_name),),
    ).fetchone()
    return row is not None


def _clear_user_state(user_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM user_audio_progress WHERE user_id=?", (int(user_id),))
        conn.execute("DELETE FROM user_audio_access_tokens WHERE user_id=?", (int(user_id),))
        conn.execute("DELETE FROM user_channel_identities WHERE user_id=?", (int(user_id),))
        if _table_exists(conn, "audio_timeline"):
            conn.execute("DELETE FROM audio_timeline WHERE user_id=?", (int(user_id),))


def _commands_from_keyboard(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    commands: list[str] = []
    for row in keyboard["buttons"]:
        for button in row:
            payload = json.loads(button["action"]["payload"])
            commands.append(str(payload["command"]))
    return commands


def test_telegram_audio_delivery_requires_bot_instance(monkeypatch):
    user_id = 195001
    _clear_user_state(user_id)
    item = AudioProgressItem(ordinal=1, anchor=11, title="Track 11", path=Path("audio/full/t11.ogg"))
    record_channel_identity(user_id, "telegram", "195001")
    monkeypatch.setattr("services.messenger.audio_delivery.get_next_audio_item", lambda uid: item)

    with pytest.raises(UnsupportedMessengerDelivery, match="Telegram bot instance is required"):
        asyncio.run(
            send_next_audio_to_user(
                user_id,
                senders=SenderRegistry(vk=_FakeSender(), max=_FakeSender()),
                fallback="telegram",
                target_platform="telegram",
            )
        )


def test_telegram_audio_delivery_sends_cached_audio_and_marks_pending(monkeypatch):
    user_id = 195002
    _clear_user_state(user_id)
    item = AudioProgressItem(ordinal=1, anchor=12, title="Track 12", path=Path("audio/full/t12.ogg"))
    record_channel_identity(user_id, "telegram", "195002")
    monkeypatch.setattr("services.messenger.audio_delivery.get_next_audio_item", lambda uid: item)
    monkeypatch.setattr("services.fast_send_audio.send_audio_cached", _fake_send_audio_cached)
    bot = _FakeTelegramBot()

    result = asyncio.run(
        send_next_audio_to_user(
            user_id,
            senders=SenderRegistry(vk=_FakeSender(), max=_FakeSender()),
            telegram_bot=bot,
            fallback="telegram",
            target_platform="telegram",
        )
    )

    assert result.transport == "telegram_audio_pending"
    assert bot.audio_calls == [(195002, item.path, "🎧 Аудио №12: Track 12")]
    snapshot = get_progress_snapshot(user_id)
    assert snapshot.pending_item is not None
    assert snapshot.pending_item.anchor == 12
    assert snapshot.last_anchor is None


def test_vk_audio_delivery_sends_native_attachment_text_controls_and_marks_pending(monkeypatch):
    user_id = 195003
    _clear_user_state(user_id)
    item = AudioProgressItem(ordinal=1, anchor=13, title="Track 13", path=Path("audio/full/t13.ogg"))
    record_channel_identity(user_id, "vk", "vk-195003")
    monkeypatch.setattr("services.messenger.audio_delivery.get_next_audio_item", lambda uid: item)

    vk_sender = _FakeSender()
    result = asyncio.run(
        send_next_audio_to_user(
            user_id,
            senders=SenderRegistry(vk=vk_sender, max=_FakeSender()),
            fallback="vk",
            target_platform="vk",
        )
    )

    assert result.transport == "vk_native_audio_pending"
    assert vk_sender.audio_calls
    external_id, file_path, caption, kwargs = vk_sender.audio_calls[0]
    assert external_id == "vk-195003"
    assert file_path == item.path
    assert "Аудио №13" in (caption or "")
    assert kwargs.get("keyboard_json")
    assert _commands_from_keyboard(kwargs["keyboard_json"]) == ["done", "progress", "history", "start"]
    assert vk_sender.text_calls
    assert "✅ Аудио №13" in vk_sender.text_calls[0][1]
    text_keyboard = vk_sender.text_calls[0][2].get("keyboard_json")
    assert text_keyboard
    assert _commands_from_keyboard(text_keyboard) == ["done", "progress", "history", "start"]
    snapshot = get_progress_snapshot(user_id)
    assert snapshot.pending_item is not None
    assert snapshot.pending_item.anchor == 13
    assert snapshot.last_anchor is None


def test_max_audio_delivery_converts_to_opus_sends_native_audio_text_controls_and_marks_pending(monkeypatch):
    user_id = 195004
    _clear_user_state(user_id)
    source = Path("audio/full/t14.mp3")
    opus = Path("audio/full/t14.opus")
    item = AudioProgressItem(ordinal=1, anchor=14, title="Track 14", path=source)
    record_channel_identity(user_id, "max", "max-195004")
    monkeypatch.setattr("services.messenger.audio_delivery.get_next_audio_item", lambda uid: item)
    monkeypatch.setattr("services.messenger.audio_delivery.ensure_max_opus_file", lambda path: opus)

    max_sender = _FakeSender()
    result = asyncio.run(
        send_next_audio_to_user(
            user_id,
            senders=SenderRegistry(vk=_FakeSender(), max=max_sender),
            fallback="max",
            target_platform="max",
        )
    )

    assert result.transport == "max_native_audio_pending"
    assert max_sender.audio_calls
    external_id, file_path, caption, kwargs = max_sender.audio_calls[0]
    assert external_id == "max-195004"
    assert file_path == opus
    assert "Аудио №14" in (caption or "")
    assert max_sender.text_calls
    assert "✅ Аудио №14" in max_sender.text_calls[0][1]
    snapshot = get_progress_snapshot(user_id)
    assert snapshot.pending_item is not None
    assert snapshot.pending_item.anchor == 14
    assert snapshot.last_anchor is None


def test_non_telegram_native_audio_failure_does_not_mark_pending(monkeypatch):
    user_id = 195005
    _clear_user_state(user_id)
    item = AudioProgressItem(ordinal=1, anchor=15, title="Track 15", path=Path("audio/full/t15.ogg"))
    record_channel_identity(user_id, "vk", "vk-195005")
    monkeypatch.setattr("services.messenger.audio_delivery.get_next_audio_item", lambda uid: item)

    with pytest.raises(UnsupportedMessengerDelivery):
        asyncio.run(
            send_next_audio_to_user(
                user_id,
                senders=SenderRegistry(vk=_FakeSender(fail_audio=True), max=_FakeSender()),
                fallback="vk",
                target_platform="vk",
            )
        )

    snapshot = get_progress_snapshot(user_id)
    assert snapshot.pending_item is None
    assert snapshot.last_anchor is None
