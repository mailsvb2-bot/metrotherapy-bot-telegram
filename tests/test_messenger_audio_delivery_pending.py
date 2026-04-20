from pathlib import Path

import pytest

from services.schema import init_db
from services.messenger import audio_delivery as delivery
from services.messenger.audio_progress import get_progress_snapshot, AudioProgressItem, mark_pending_audio_delivery
from services.messenger.outbound import SenderRegistry


class DummyTelegramBot:
    pass


@pytest.fixture(autouse=True)
def _init_db():
    init_db()


@pytest.mark.asyncio
async def test_telegram_native_delivery_marks_pending(monkeypatch):
    item = AudioProgressItem(ordinal=1, anchor=31, title='A31', path=Path('audio/full/a31.opus'))
    monkeypatch.setattr(delivery, 'get_next_audio_item', lambda user_id: item)

    async def fake_send_audio_cached(bot, external_user_id, item):
        return {'ok': True}

    monkeypatch.setattr(delivery, '_send_telegram_audio', fake_send_audio_cached)

    registry = SenderRegistry()
    result = await delivery.send_next_audio_to_user(940001, senders=registry, telegram_bot=DummyTelegramBot(), target_platform='telegram', fallback='telegram')

    assert result.transport == 'telegram_audio_pending'
    snap = get_progress_snapshot(940001)
    assert snap.last_anchor is None
    assert snap.pending_item is not None
    assert snap.pending_item.anchor == 31


@pytest.mark.asyncio
async def test_continue_reuses_pending_item_before_advancing(monkeypatch):
    item = AudioProgressItem(ordinal=1, anchor=41, title='A41', path=Path('audio/full/a41.opus'))

    async def fake_send_audio_cached(bot, external_user_id, item):
        return {'ok': True}

    monkeypatch.setattr(delivery, '_send_telegram_audio', fake_send_audio_cached)
    monkeypatch.setattr(delivery, 'get_next_audio_item', lambda user_id: AudioProgressItem(ordinal=2, anchor=42, title='A42', path=Path('audio/full/a42.opus')))

    registry = SenderRegistry()
    mark_pending_audio_delivery(940002, item=item, platform='telegram', token=None)

    result = await delivery.send_next_audio_to_user(940002, senders=registry, telegram_bot=DummyTelegramBot(), target_platform='telegram', fallback='telegram')

    assert '№41' in result.message
    snap = get_progress_snapshot(940002)
    assert snap.pending_item is not None
    assert snap.pending_item.anchor == 41
