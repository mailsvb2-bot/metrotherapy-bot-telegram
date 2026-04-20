from pathlib import Path

from services.schema import init_db
from services.db import db
from services.messenger.audio_progress import AudioProgressItem, get_progress_snapshot
from services.messenger.preferences import record_channel_identity
from services.messenger.outbound import SenderRegistry
from services.messenger.audio_delivery import send_next_audio_to_user


def setup_module(module):
    init_db()


class _FakeVkSender:
    def __init__(self):
        self.text_calls = []

    async def send_text(self, external_user_id: str, text: str, **kwargs):
        self.text_calls.append((external_user_id, text, kwargs))
        return {'ok': True}


class _NoopSender:
    async def send_text(self, external_user_id: str, text: str, **kwargs):
        return {'ok': True}

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs):
        return {'ok': True}


def _clear_user_state(user_id: int) -> None:
    with db() as conn:
        conn.execute('DELETE FROM user_audio_progress WHERE user_id=?', (int(user_id),))
        conn.execute('DELETE FROM user_audio_access_tokens WHERE user_id=?', (int(user_id),))
        conn.execute('DELETE FROM user_channel_identities WHERE user_id=?', (int(user_id),))


def test_link_delivery_marks_pending_and_reuses_same_item(monkeypatch):
    user_id = 194101
    _clear_user_state(user_id)
    item = AudioProgressItem(ordinal=1, anchor=51, title='Track 51', path=Path('audio/full/t51.opus'))
    record_channel_identity(user_id, 'vk', 'vk-user-94101')
    monkeypatch.setattr('services.messenger.audio_delivery.get_next_audio_item', lambda uid: item)
    monkeypatch.setattr('services.messenger.audio_delivery.build_audio_access_url', lambda token: f'https://example.test/media/{token}')

    sender = _FakeVkSender()
    result1 = __import__('asyncio').run(
        send_next_audio_to_user(user_id, senders=SenderRegistry(vk=sender, telegram=_NoopSender(), max=_NoopSender()), fallback='vk', target_platform='vk')
    )
    assert result1.transport == 'messenger_link'
    snap1 = get_progress_snapshot(user_id)
    assert snap1.pending_item is not None and snap1.pending_item.anchor == 51

    monkeypatch.setattr('services.messenger.audio_delivery.get_next_audio_item', lambda uid: AudioProgressItem(ordinal=2, anchor=52, title='Track 52', path=Path('audio/full/t52.opus')))
    result2 = __import__('asyncio').run(
        send_next_audio_to_user(user_id, senders=SenderRegistry(vk=sender, telegram=_NoopSender(), max=_NoopSender()), fallback='vk', target_platform='vk')
    )
    assert '№51' in result2.message
    snap2 = get_progress_snapshot(user_id)
    assert snap2.pending_item is not None and snap2.pending_item.anchor == 51
    assert len(sender.text_calls) == 2
