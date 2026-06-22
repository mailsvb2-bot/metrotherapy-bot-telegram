from pathlib import Path

from services.schema import init_db
from services.messenger.audio_progress import get_next_audio_item, record_audio_delivery, get_progress_snapshot, AudioProgressItem
from services.messenger.bridge import issue_bridge_token
from services.messenger.entrypoints import register_user_entry


def setup_module(module):
    init_db()


def test_audio_progress_continues_after_bridge_linking():
    token = issue_bridge_token(1001)
    linked = register_user_entry(555001, platform='vk', external_user_id='vk-555001', start_payload=f'bridge_{token}')
    assert linked.user_id == 1001
    item = AudioProgressItem(ordinal=1, anchor=1, title='Intro', path=Path('audio/full/intro.opus'))
    record_audio_delivery(1001, item=item, platform='telegram')
    snap = get_progress_snapshot(1001)
    assert snap.last_anchor == item.anchor
    assert snap.last_platform == 'telegram'


def test_progress_snapshot_empty_before_start():
    snap = get_progress_snapshot(1002)
    assert snap.last_anchor is None
    # Catalog can be empty in some environments; snapshot must stay well-formed regardless.
    assert snap.sequence_key == 'full_series'


def test_progress_snapshot_shows_pending_item_before_confirmation():
    item = AudioProgressItem(ordinal=1, anchor=20, title='A20', path=Path('audio/full/a20.opus'))
    from services.messenger.audio_access import issue_or_reuse_audio_access_token
    token = issue_or_reuse_audio_access_token(1003, item=item, platform='vk')
    assert token
    snap = get_progress_snapshot(1003)
    assert snap.last_anchor is None
    assert snap.pending_item is not None
    assert snap.pending_item.anchor == 20
