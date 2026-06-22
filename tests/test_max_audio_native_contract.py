from __future__ import annotations

from pathlib import Path

import pytest

from services.messenger.audio_delivery import send_next_audio_to_user
from services.messenger.audio_progress import AudioProgressItem, AudioProgressSnapshot
from services.messenger.outbound import DeliveryPlan, SenderRegistry, UnsupportedMessengerDelivery
from services.messenger.platforms import MessengerPlatform


class _FailingMaxSender:
    async def send_audio_file(self, *args, **kwargs):
        raise RuntimeError("native audio failed")

    async def send_text(self, *args, **kwargs):
        raise AssertionError("MAX must not fall back to a text/audio link")


@pytest.mark.asyncio
async def test_max_audio_delivery_refuses_link_fallback(monkeypatch) -> None:
    item = AudioProgressItem(
        ordinal=1,
        anchor=1,
        title="test opus",
        path=Path("audio/full/001 test.opus"),
    )

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
    monkeypatch.setattr("services.messenger.audio_delivery.log_audio_timeline_event", lambda *args, **kwargs: None)

    senders = SenderRegistry(max=_FailingMaxSender())

    with pytest.raises(UnsupportedMessengerDelivery, match="Не удалось отправить аудио прямо"):
        await send_next_audio_to_user(
            1,
            senders=senders,
            fallback=MessengerPlatform.MAX.value,
            target_platform=MessengerPlatform.MAX.value,
        )
