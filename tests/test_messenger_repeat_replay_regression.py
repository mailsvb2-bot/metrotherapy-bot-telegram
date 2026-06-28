from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.messenger import audio_replay, reply_dispatcher
from services.messenger.audio_delivery import AudioDeliveryResult
from services.messenger.audio_progress import AudioProgressItem
from services.messenger.outbound import DeliveryPlan, SenderRegistry
from services.messenger.text_ui import MessengerReply


class _FakeSender:
    async def send_text(self, external_user_id: str, text: str, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "external_user_id": external_user_id, "text": text, "kwargs": kwargs}

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "external_user_id": external_user_id, "file_path": str(file_path), "caption": caption, "kwargs": kwargs}


@dataclass(frozen=True)
class _Snapshot:
    pending_item: AudioProgressItem | None
    last_anchor: int | None


def test_next_audio_reply_with_replay_meta_uses_replay_delivery(monkeypatch):
    captured: dict[str, Any] = {"next_calls": 0, "replay_calls": 0}
    fake_sender = _FakeSender()

    async def fake_next_audio(*args: Any, **kwargs: Any) -> AudioDeliveryResult:
        captured["next_calls"] += 1
        return AudioDeliveryResult(user_id=123, platform="max", item=None, transport="none", message="next")

    async def fake_replay_audio(*args: Any, **kwargs: Any) -> AudioDeliveryResult:
        captured["replay_calls"] += 1
        captured["replay_anchor"] = kwargs.get("anchor")
        return AudioDeliveryResult(user_id=123, platform="max", item=None, transport="max_native_audio_replay", message="replay")

    monkeypatch.setattr(reply_dispatcher, "MaxBotSender", lambda: fake_sender)
    monkeypatch.setattr(reply_dispatcher, "send_next_audio_to_user", fake_next_audio)
    monkeypatch.setattr(reply_dispatcher, "send_replay_audio_to_user", fake_replay_audio)

    asyncio.run(
        reply_dispatcher.send_reply_bundle(
            "max",
            "max-ext-123",
            123,
            [MessengerReply(kind="next_audio", meta={"replay": "1", "replay_anchor": "7"})],
        )
    )

    assert captured["replay_calls"] == 1
    assert captured["replay_anchor"] == 7
    assert captured["next_calls"] == 0


def test_replay_helper_reuses_last_confirmed_item_without_pending_reset(monkeypatch, tmp_path):
    item_1 = AudioProgressItem(ordinal=1, anchor=1, title="Morning", path=tmp_path / "01_morning.ogg")
    item_2 = AudioProgressItem(ordinal=2, anchor=2, title="Evening", path=tmp_path / "02_evening.ogg")
    captured: dict[str, Any] = {}

    def fake_item_by_anchor(anchor: int) -> AudioProgressItem | None:
        return {1: item_1, 2: item_2}.get(int(anchor))

    async def fake_send_non_telegram_native(**kwargs: Any) -> AudioDeliveryResult:
        captured.update(kwargs)
        item = kwargs["item"]
        return AudioDeliveryResult(
            user_id=int(kwargs["user_id"]),
            platform=str(kwargs["platform"]),
            item=item,
            transport=f"{kwargs['platform']}_native_audio_replay",
            message="replayed",
        )

    monkeypatch.setattr(
        audio_replay,
        "build_delivery_plan",
        lambda user_id, **kwargs: DeliveryPlan(user_id=int(user_id), platform="max", external_user_id="max-ext-42"),
    )
    monkeypatch.setattr(audio_replay, "get_progress_snapshot", lambda user_id: _Snapshot(pending_item=None, last_anchor=1))
    monkeypatch.setattr(audio_replay, "get_audio_item_by_anchor", fake_item_by_anchor)
    monkeypatch.setattr(audio_replay, "_send_non_telegram_native", fake_send_non_telegram_native)

    result = asyncio.run(
        audio_replay.send_replay_audio_to_user(
            42,
            senders=SenderRegistry(max=_FakeSender()),
            target_platform="max",
            fallback="max",
        )
    )

    assert result.item == item_1
    assert captured["item"] == item_1
    # Replay passes a marker as ``pending`` so the lower delivery layer does not
    # convert an already confirmed track back into the current pending step.
    assert captured["pending"] == item_1
    assert captured["item"] != item_2
    assert captured["replay"] is True
