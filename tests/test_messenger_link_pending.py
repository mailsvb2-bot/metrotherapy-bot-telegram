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



