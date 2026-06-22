from services.db import db
from services.migrations import apply_all_migrations
from services.messenger.preferences import record_channel_identity
from services.messenger.text_ui import handle_incoming_text
from services.delivery_preferences import get_delivery_preferences


def _migrate():
    with db() as conn:
        apply_all_migrations(conn)


def test_channel_command_sets_slot_preference():
    _migrate()
    record_channel_identity(701, 'telegram', '701')
    record_channel_identity(701, 'max', 'mx701')
    user_id, replies = handle_incoming_text(701, platform='telegram', external_user_id='701', text='channel morning max')
    prefs = get_delivery_preferences(user_id)
    assert prefs.morning_channel == 'max'
    assert 'утренних' in replies[0].text
    assert 'MAX' in replies[0].text


def test_time_command_shows_resolved_channels():
    _migrate()
    record_channel_identity(702, 'telegram', '702')
    user_id, replies = handle_incoming_text(702, platform='telegram', external_user_id='702', text='time')
    assert user_id == 702
    assert 'Утреннее касание сейчас пойдёт через' in replies[0].text
