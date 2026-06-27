from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import settings
from runtime.messenger_transport_errors import MessengerTransportError
from runtime.messenger_vk_sender import _callback_keyboard_json
from runtime.messenger_vk_ui import prepare_vk_keyboard_json
from services.db import db
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
        self.audio_calls: list[tuple[str, Path, str | None, dict[str, Any]]] = []
        self.text_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any) -> dict[str, Any]:
        self.audio_calls.append((str(external_user_id), Path(file_path), caption, dict(kwargs)))
        if self.fail_audio:
            raise MessengerTransportError("Unexpected VK upload response for type=doc: {'error': 'wrong_music_file'}")
        return {"ok": True}

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any) -> dict[str, Any]:
        stored = dict(kwargs)
        keyboard_json = stored.get("keyboard_json")
        if keyboard_json:
            rendered = prepare_vk_keyboard_json(str(keyboard_json), external_user_id=str(external_user_id), text=str(text or ""))
            stored["keyboard_json"] = _callback_keyboard_json(rendered)
        self.text_calls.append((str(external_user_id), str(text), stored))
        return {"ok": True}


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (str(table_name),)).fetchone()
    return row is not None


def _clear_user_state(user_id: int) -> None:
    with db() as conn:
        for table in (
            "users",
            "events",
            "mood_sessions",
            "user_audio_progress",
            "user_audio_access_tokens",
            "user_channel_identities",
            "audio_timeline",
            "state_ratings",
            "pending_inputs",
        ):
            if _table_exists(conn, table):
                conn.execute(f"DELETE FROM {table} WHERE user_id=?", (int(user_id),))


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


def _open_link_actions(keyboard_json: str) -> list[dict[str, Any]]:
    keyboard = json.loads(keyboard_json)
    return [
        button["action"]
        for row in keyboard["buttons"]
        for button in row
        if button["action"].get("type") == "open_link"
    ]


def _latest_keyboard(sender: _FakeVkSender) -> str:
    for _, _, kwargs in reversed(sender.text_calls):
        keyboard = kwargs.get("keyboard_json")
        if keyboard:
            return str(keyboard)
    raise AssertionError("no VK keyboard captured")


async def _dispatch_vk(user_id: int, sender: _FakeVkSender, text: str) -> list[MessengerReply]:
    canonical_user_id, replies = handle_incoming_text(
        user_id,
        platform="vk",
        external_user_id=str(user_id),
        text=text,
    )
    await send_reply_bundle("vk", str(user_id), canonical_user_id, replies)
    return replies


def test_vk_full_user_journey_score_audio_done_pay_gift(monkeypatch, tmp_path):
    user_id = 918001
    _clear_user_state(user_id)

    audio_path = tmp_path / "01_morning.ogg"
    audio_path.write_bytes(b"fake-vk-audio")
    item = AudioProgressItem(ordinal=1, anchor=1, title="Morning Route", path=audio_path)
    anchored = _AnchoredAudio(anchor=1, clean_title="Morning Route", path=audio_path)

    sender = _FakeVkSender(fail_audio=True)
    monkeypatch.setattr(settings, "MESSENGER_PUBLIC_BASE_URL", "https://example.test")
    monkeypatch.setattr("services.messenger.audio_progress.list_full_series", lambda: [item])
    monkeypatch.setattr("services.mood_text_flow.get_by_anchor", lambda anchor: anchored if int(anchor) == 1 else None)
    monkeypatch.setattr("services.messenger.reply_dispatcher.VkBotSender", lambda: sender)
    monkeypatch.setattr("services.messenger.reply_dispatcher.build_vk_mood_progress_chart_path", lambda uid: None)

    asyncio.run(_dispatch_vk(user_id, sender, "start"))
    assert "Главное меню" in sender.text_calls[-1][1]

    asyncio.run(_dispatch_vk(user_id, sender, "demo"))
    assert "Бесплатная практика" in sender.text_calls[-1][1]

    asyncio.run(_dispatch_vk(user_id, sender, "demo_work"))
    score_keyboard = _latest_keyboard(sender)
    assert json.loads(score_keyboard)["inline"] is False
    assert _commands(score_keyboard)[:21] == [str(value) for value in range(-10, 11)]
    assert _labels(score_keyboard)[:21] == [f"{value:+d}" if value else "0" for value in range(-10, 11)]

    asyncio.run(_dispatch_vk(user_id, sender, "-9"))
    assert sender.audio_calls, "pre-score must attempt VK native audio first"
    access_text = sender.text_calls[-1][1]
    assert "https://example.test/media/audio/access/" in access_text
    assert "✅ Прослушал" in access_text
    assert get_pending_audio_token(user_id)
    snapshot = get_progress_snapshot(user_id)
    assert snapshot.pending_item is not None
    assert snapshot.pending_item.anchor == 1

    asyncio.run(_dispatch_vk(user_id, sender, "done"))
    post_keyboard = _latest_keyboard(sender)
    assert "Теперь оцените состояние ПОСЛЕ" in sender.text_calls[-1][1]
    assert _commands(post_keyboard)[:21] == [str(value) for value in range(-10, 11)]

    asyncio.run(_dispatch_vk(user_id, sender, "3"))
    joined_texts = "\n".join(text for _, text, _ in sender.text_calls[-3:])
    assert "Оценку после прослушивания +3 сохранил" in joined_texts

    asyncio.run(_dispatch_vk(user_id, sender, "pay"))
    pay_keyboard = _latest_keyboard(sender)
    pay_links = _open_link_actions(pay_keyboard)
    assert pay_links
    assert all(len(action.get("payload") or "") <= 255 for action in pay_links)
    assert all(str(action.get("link") or "").startswith("https://example.test/") for action in pay_links)

    asyncio.run(_dispatch_vk(user_id, sender, "gift"))
    gift_keyboard = _latest_keyboard(sender)
    gift_links = _open_link_actions(gift_keyboard)
    assert gift_links
    assert all(len(action.get("payload") or "") <= 255 for action in gift_links)
    assert all(str(action.get("link") or "").startswith("https://example.test/") for action in gift_links)
