from pathlib import Path

from services.accounts.audio_progress import get_audio_state
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

def test_record_audio_delivery_writes_account_audio_progress():
    item = AudioProgressItem(ordinal=1, anchor=7, title='A7', path=Path('audio/full/a7.opus'))

    record_audio_delivery(101001, item=item, platform='telegram')

    state = get_audio_state(101001)
    assert state.last_completed_audio_no == 7
    assert state.pending_audio_no is None


def test_legacy_source_delivery_updates_canonical_account_progress():
    token = issue_bridge_token(101010)
    linked = register_user_entry(
        202020,
        platform='vk',
        external_user_id='202020',
        start_payload=f'bridge_{token}',
    )
    assert linked.user_id == 101010

    item = AudioProgressItem(ordinal=1, anchor=8, title='A8', path=Path('audio/full/a8.opus'))

    record_audio_delivery(202020, item=item, platform='vk')

    state = get_audio_state(101010)
    assert state.last_completed_audio_no == 8

    snap = get_progress_snapshot(101010)
    assert snap.last_anchor == 8
    assert snap.last_platform == 'vk'

