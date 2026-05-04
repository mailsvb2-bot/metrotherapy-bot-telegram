from __future__ import annotations

from pathlib import Path

import pytest

from services.messenger.audio_delivery import send_next_audio_to_user
from services.messenger.audio_progress import AudioProgressItem, AudioProgressSnapshot
from services.messenger.outbound import DeliveryPlan, SenderRegistry
from services.messenger.platforms import MessengerPlatform


class _CapturingMaxSender:
    def __init__(self) -> None:
        self.audio_paths: list[Path] = []
        self.texts: list[str] = []

    async def send_audio_file(self, external_user_id, file_path, **kwargs):
        self.audio_paths.append(Path(file_path))
        return {"ok": True}

    async def send_text(self, external_user_id, text, **kwargs):
        self.texts.append(str(text))
        return {"ok": True}


@pytest.mark.asyncio
async def test_max_delivery_prepares_opus_before_sending(monkeypatch) -> None:
    source = Path("audio/full/001 source.mp3")
    prepared = Path("data/max_opus_cache/001_source.opus")
    item = AudioProgressItem(ordinal=1, anchor=1, title="source", path=source)

    monkeypatch.setattr(
        "services.messenger.audio_delivery.build_delivery_plan",
        lambda *args, **kwargs: DeliveryPlan(
            platform=MessengerPlatform.MAX.value,
            external_user_id="12345",
            reason="test",
        ),
    )
    monkeypatch.setattr(
        "services.messenger.audio_delivery.get_progress_snapshot",
        lambda user_id: AudioProgressSnapshot(
            user_id=int(user_id),
            sequence_key="full_series",
            last_anchor=None,
            last_title=None,
            last_platform=None,
            last_confirmed_at=None,
            pending_item=None,
            pending_platform=None,
            pending_delivered_at=None,
            next_item=item,
        ),
    )
    monkeypatch.setattr("services.messenger.audio_delivery.get_next_audio_item", lambda user_id: item)
    monkeypatch.setattr("services.messenger.audio_delivery.ensure_max_opus_file", lambda path: prepared)
    monkeypatch.setattr("services.messenger.audio_delivery.mark_pending_audio_delivery", lambda *args, **kwargs: None)
    monkeypatch.setattr("services.messenger.audio_delivery.log_audio_timeline_event", lambda *args, **kwargs: None)

    sender = _CapturingMaxSender()
    result = await send_next_audio_to_user(
        1,
        senders=SenderRegistry(max=sender),
        fallback=MessengerPlatform.MAX.value,
        target_platform=MessengerPlatform.MAX.value,
    )

    assert result.transport == "max_native_audio_pending"
    assert sender.audio_paths == [prepared]
    assert all(path.suffix == ".opus" for path in sender.audio_paths)
    assert sender.texts
