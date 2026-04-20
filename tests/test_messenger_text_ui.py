from services.messenger.text_ui import handle_incoming_text
from services.messenger.preferences import get_channel_snapshot
from services.schema import init_db


def setup_module(module):
    init_db()


def test_platform_command_updates_preference():
    canonical_user_id, replies = handle_incoming_text(901001, platform='vk', external_user_id='901001', text='/platform max')
    assert canonical_user_id == 901001
    assert replies
    snapshot = get_channel_snapshot(901001)
    assert snapshot['preferred_platform'] == 'max'


def test_share_command_returns_targets_text():
    canonical_user_id, replies = handle_incoming_text(901002, platform='max', external_user_id='901002', text='share')
    assert canonical_user_id == 901002
    assert replies
    assert 'Поделиться' in replies[0].text or 'пока не настроены' in replies[0].text


def test_continue_command_returns_next_audio_action():
    canonical_user_id, replies = handle_incoming_text(901003, platform='telegram', external_user_id='901003', text='continue')
    assert canonical_user_id == 901003
    assert replies[0].kind == 'next_audio'
