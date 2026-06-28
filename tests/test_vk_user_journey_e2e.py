from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import settings
from runtime.messenger_vk_sender import _callback_keyboard_json
from runtime.messenger_vk_ui import prepare_vk_keyboard_json
from services.messenger.audio_progress import AudioProgressItem, get_pending_audio_token, get_progress_snapshot
from services.messenger.reply_dispatcher import send_reply_bundle
from services.messenger.text_ui import MessengerReply, handle_incoming_text


@dataclass(frozen=True)
class _AnchoredAudio:
    anchor: int
    clean_title: str
    path: Path


class _FakeVkSender:
    def __init__(self, *, fail_audio: bool = True) -> None:
        self.fail_audio = fail_audio
        self.fail_next_text = False
        self.audio_calls: list[tuple[str, Path, str | None, dict[str, Any]]] = []
        self.text_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any) -> dict[str, Any]:
        self.audio_calls.append((str(external_user_id), Path(file_path), caption, dict(kwargs)))
        if self.fail_audio:
            raise RuntimeError("VK rejected this audio attachment")
        return {"ok": True}

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any) -> dict[str, Any]:
        if self.fail_next_text:
            self.fail_next_text = False
            raise RuntimeError("VK rejected post-audio controls")
        stored = dict(kwargs)
        keyboard_json = stored.get("keyboard_json")
        if keyboard_json:
            rendered = prepare_vk_keyboard_json(str(keyboard_json), external_user_id=str(external_user_id), text=str(text or ""))
            stored["keyboard_json"] = _callback_keyboard_json(rendered)
        self.text_calls.append((str(external_user_id), str(text), stored))
        return {"ok": True}


def _commands(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    commands: list[str] = []
    for row in keyboard["buttons"]:
        for button in row:
            payload = json.loads(button["action"].get("payload") or "{}")
            commands.append(str(payload.get("command") or ""))
    return commands


def _labels(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    return [button["action"]["label"] for row in keyboard["buttons"] for button in row]


def _latest_keyboard(sender: _FakeVkSender) -> str:
    for _, _, kwargs in reversed(sender.text_calls):
        keyboard = kwargs.get("keyboard_json")
        if keyboard:
            return str(keyboard)
    raise AssertionError("no VK keyboard captured")


async def _dispatch_vk(user_id: int, text: str) -> list[MessengerReply]:
    canonical_user_id, replies = handle_incoming_text(
        user_id,
        platform="vk",
        external_user_id=str(user_id),
        text=text,
    )
    await send_reply_bundle("vk", str(user_id), canonical_user_id, replies)
    return replies


def test_vk_audio_rejection_uses_link_fallback(monkeypatch, tmp_path):
    user_id = 918000000 + (os.getpid() % 100000)
    audio_path = tmp_path / "01_morning.ogg"
    audio_path.write_bytes(b"fake-vk-audio")
    item = AudioProgressItem(ordinal=1, anchor=1, title="Morning Route", path=audio_path)
    anchored = _AnchoredAudio(anchor=1, clean_title="Morning Route", path=audio_path)

    sender = _FakeVkSender(fail_audio=True)
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setattr(settings, "MESSENGER_PUBLIC_BASE_URL", "https://example.test", raising=False)
    monkeypatch.setattr("services.messenger.audio_progress.list_full_series", lambda: [item])
    monkeypatch.setattr("services.mood_text_flow.get_by_anchor", lambda anchor: anchored if int(anchor) == 1 else None)
    monkeypatch.setattr("services.messenger.reply_dispatcher.VkBotSender", lambda: sender)

    asyncio.run(_dispatch_vk(user_id, "start"))
    asyncio.run(_dispatch_vk(user_id, "demo"))
    asyncio.run(_dispatch_vk(user_id, "demo_work"))
    score_keyboard = _latest_keyboard(sender)
    assert json.loads(score_keyboard)["inline"] is False
    assert _commands(score_keyboard)[:21] == [str(value) for value in range(-10, 11)]

    asyncio.run(_dispatch_vk(user_id, "-9"))
    assert sender.audio_calls
    access_text = sender.text_calls[-1][1]
    assert "media/audio/access" in access_text
    assert get_pending_audio_token(user_id)


def test_vk_native_audio_success_is_not_rolled_back_when_notice_fails(monkeypatch, tmp_path):
    user_id = 919000000 + (os.getpid() % 100000)
    audio_path = tmp_path / "01_morning.ogg"
    audio_path.write_bytes(b"fake-vk-audio")
    item = AudioProgressItem(ordinal=1, anchor=1, title="Morning Route", path=audio_path)
    anchored = _AnchoredAudio(anchor=1, clean_title="Morning Route", path=audio_path)

    sender = _FakeVkSender(fail_audio=False)
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setattr(settings, "MESSENGER_PUBLIC_BASE_URL", "https://example.test", raising=False)
    monkeypatch.setattr("services.messenger.audio_progress.list_full_series", lambda: [item])
    monkeypatch.setattr("services.mood_text_flow.get_by_anchor", lambda anchor: anchored if int(anchor) == 1 else None)
    monkeypatch.setattr("services.messenger.reply_dispatcher.VkBotSender", lambda: sender)

    asyncio.run(_dispatch_vk(user_id, "start"))
    asyncio.run(_dispatch_vk(user_id, "demo"))
    asyncio.run(_dispatch_vk(user_id, "demo_work"))

    sender.fail_next_text = True
    before_texts = len(sender.text_calls)
    asyncio.run(_dispatch_vk(user_id, "-4"))

    assert sender.audio_calls
    assert get_pending_audio_token(user_id) is None
    snapshot = get_progress_snapshot(user_id)
    assert snapshot.pending_item is not None
    assert snapshot.pending_item.anchor == 1
    new_texts = "\n".join(text for _, text, _ in sender.text_calls[before_texts:])
    assert "media/audio/access" not in new_texts
