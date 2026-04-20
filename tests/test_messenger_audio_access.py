from pathlib import Path

from services.schema import init_db
from services.messenger.audio_access import issue_or_reuse_audio_access_token, register_audio_access, get_audio_access_grant
from services.messenger.audio_progress import AudioProgressItem, get_progress_snapshot


def setup_module(module):
    init_db()


def test_reuses_pending_token_until_first_access():
    item = AudioProgressItem(ordinal=1, anchor=10, title='A10', path=Path('audio/full/a10.opus'))
    token1 = issue_or_reuse_audio_access_token(8111, item=item, platform='vk')
    token2 = issue_or_reuse_audio_access_token(8111, item=item, platform='vk')
    assert token1 == token2
    snap = get_progress_snapshot(8111)
    assert snap.last_anchor is None
    assert snap.pending_item is not None
    assert snap.pending_item.anchor == 10


def test_first_access_confirms_progress_once():
    item = AudioProgressItem(ordinal=1, anchor=11, title='A11', path=Path('audio/full/a11.opus'))
    token = issue_or_reuse_audio_access_token(8112, item=item, platform='max')
    grant1 = register_audio_access(token)
    grant2 = register_audio_access(token)
    assert grant1 is not None and grant2 is not None
    refreshed = get_audio_access_grant(token)
    assert refreshed is not None
    assert refreshed.access_count == 2
    snap = get_progress_snapshot(8112)
    assert snap.last_anchor == 11
    assert snap.pending_item is None
