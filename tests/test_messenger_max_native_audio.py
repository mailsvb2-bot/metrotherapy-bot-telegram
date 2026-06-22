from pathlib import Path

from services.schema import init_db
from services.db import db
from services.messenger.audio_progress import AudioProgressItem, get_progress_snapshot
from services.messenger.preferences import record_channel_identity
from services.messenger.outbound import SenderRegistry
from services.messenger.audio_delivery import send_next_audio_to_user


def setup_module(module):
    init_db()


class _FakeMaxSender:
    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.audio_calls = []
        self.text_calls = []

    async def send_text(self, external_user_id: str, text: str, **kwargs):
        self.text_calls.append((external_user_id, text, kwargs))
        return {'ok': True}

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs):
        self.audio_calls.append((external_user_id, file_path, caption, kwargs))
        if self.should_fail:
            raise RuntimeError('boom')
        return {'ok': True}


def _clear_user_state(user_id: int) -> None:
    with db() as conn:
        conn.execute('DELETE FROM user_audio_progress WHERE user_id=?', (int(user_id),))
        conn.execute('DELETE FROM user_audio_access_tokens WHERE user_id=?', (int(user_id),))


class _NoopSender:
    async def send_text(self, external_user_id: str, text: str, **kwargs):
        return {'ok': True}

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs):
        return {'ok': True}


def test_max_prefers_native_audio(monkeypatch):
    user_id = 194001
    _clear_user_state(user_id)
    item = AudioProgressItem(ordinal=1, anchor=41, title='Track 41', path=Path('audio/full/t41.opus'))
    record_channel_identity(user_id, 'max', 'max-user-94001')
    monkeypatch.setattr('services.messenger.audio_delivery.get_next_audio_item', lambda uid: item)
    monkeypatch.setattr('services.messenger.audio_delivery.ensure_max_opus_file', lambda path: item.path)

    sender = _FakeMaxSender()
    result = __import__('asyncio').run(
        send_next_audio_to_user(user_id, senders=SenderRegistry(max=sender, telegram=_NoopSender(), vk=_NoopSender()), fallback='max', target_platform='max')
    )
    assert result.transport == 'max_native_audio_pending'
    assert sender.audio_calls
    assert sender.text_calls
    snap = get_progress_snapshot(user_id)
    assert snap.last_anchor is None
    assert snap.pending_item is not None and snap.pending_item.anchor == 41



