from pathlib import Path

import pytest

from services.schema import init_db
from services.messenger import audio_delivery as delivery
from services.messenger.audio_progress import (
    AudioProgressItem,
    get_next_audio_item,
    get_progress_snapshot,
    mark_pending_audio_delivery,
    record_audio_delivery,
)
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


def test_full_audio_sequence_ends_for_regular_users(monkeypatch):
    first = AudioProgressItem(ordinal=1, anchor=1, title='Morning', path=Path('audio/full/1_morning.opus'))
    second = AudioProgressItem(ordinal=2, anchor=2, title='Evening', path=Path('audio/full/2_evening.opus'))
    monkeypatch.setattr('services.messenger.audio_progress.list_full_series', lambda: [first, second])

    user_id = 940003
    record_audio_delivery(user_id, item=first, platform='vk')
    assert get_next_audio_item(user_id).anchor == 2

    record_audio_delivery(user_id, item=second, platform='vk')
    assert get_next_audio_item(user_id) is None


def test_full_audio_sequence_loops_only_for_configured_admins(monkeypatch):
    first = AudioProgressItem(ordinal=1, anchor=1, title='Morning', path=Path('audio/full/1_morning.opus'))
    second = AudioProgressItem(ordinal=2, anchor=2, title='Evening', path=Path('audio/full/2_evening.opus'))
    monkeypatch.setattr('services.messenger.audio_progress.list_full_series', lambda: [first, second])
    monkeypatch.setattr('services.messenger.audio_progress._can_loop_audio', lambda user_id: int(user_id) == 940004)

    user_id = 940004
    record_audio_delivery(user_id, item=first, platform='vk')
    assert get_next_audio_item(user_id).anchor == 2

    record_audio_delivery(user_id, item=second, platform='vk')
    next_item = get_next_audio_item(user_id)

    assert next_item is not None
    assert next_item.anchor == 1
