from datetime import datetime, timezone

from services.db import db
from services.migrations import apply_all_migrations
from services.messenger.preferences import record_channel_identity
from services.messenger.text_ui import handle_incoming_text
from services.mood import create_session
from services.auto_audio import _is_due_for_user
from services.mood_text_flow import parse_score_text, find_pending_pre_session_id


def _migrate():
    with db() as conn:
        apply_all_migrations(conn)


def test_parse_score_text_accepts_plain_number():
    assert parse_score_text('3') == 3
    assert parse_score_text('-10') == -10
    assert parse_score_text('11') is None
    assert parse_score_text('hello') is None


def test_handle_incoming_text_detects_pending_pre_score():
    _migrate()
    record_channel_identity(801, 'max', 'mx801')
    sid = create_session(801, kind='work', source='auto', day='2026-04-16', slot='morning', scheduled_at='2026-04-16:08:30', anchor_id=1)
    user_id, replies = handle_incoming_text(801, platform='max', external_user_id='mx801', text='4')
    assert sid
    assert user_id == 801
    assert replies[0].kind == 'auto_pre_score'
    assert replies[0].meta['score'] == '4'
    assert find_pending_pre_session_id(801) == sid


def test_is_due_for_user_uses_slot_time():
    _migrate()
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO users(user_id, work_time, home_time) VALUES(?,?,?)', (802, '08:30', '19:00'))
    due, tz_name, hm = _is_due_for_user(802, 'morning', datetime(2026, 4, 16, 8, 30, 1, tzinfo=timezone.utc))
    assert hm == '08:30'
    assert isinstance(tz_name, str)
    assert due in {True, False}


def test_is_due_for_user_respects_quiet_hours_block():
    _migrate()
    with db() as conn:
        conn.execute('INSERT OR REPLACE INTO users(user_id, work_time, home_time) VALUES(?,?,?)', (803, '08:30', '19:00'))
    from services.delivery_preferences import set_user_timezone, set_quiet_hours
    set_user_timezone(803, 'UTC')
    set_quiet_hours(803, '22:00', '09:00')
    due, tz_name, hm = _is_due_for_user(803, 'morning', datetime(2026, 4, 16, 8, 30, 1, tzinfo=timezone.utc))
    assert hm == '08:30'
    assert tz_name == 'UTC'
    assert due is False
