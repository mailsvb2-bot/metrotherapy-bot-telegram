from services.schema import init_db
from services.messenger.timeline import (
    log_audio_timeline_event,
    get_messenger_stage_overview,
    get_messenger_policy_overview,
)
from services.mood import create_session, set_pre, mark_audio_sent


def setup_module(module):
    init_db()


def test_stage_overview_aggregates_funnel_by_platform():
    uid = 970001
    log_audio_timeline_event(uid, event_type='pre_score_received', sequence_key='full_series', platform='max')
    log_audio_timeline_event(uid, event_type='link_sent', sequence_key='full_series', platform='max')
    log_audio_timeline_event(uid, event_type='access_confirmed', sequence_key='full_series', platform='max')
    log_audio_timeline_event(uid, event_type='post_score_received', sequence_key='full_series', platform='max')
    log_audio_timeline_event(uid + 1, event_type='pre_score_received', sequence_key='full_series', platform='vk')
    log_audio_timeline_event(uid + 1, event_type='native_audio_sent', sequence_key='full_series', platform='vk')

    overview = get_messenger_stage_overview()

    assert overview['per_platform']['max']['pre_score'] >= 1
    assert overview['per_platform']['max']['audio_sent'] >= 1
    assert overview['per_platform']['max']['confirmed'] >= 1
    assert overview['per_platform']['max']['post_score'] >= 1
    assert overview['per_platform']['vk']['pre_score'] >= 1
    assert overview['per_platform']['vk']['audio_sent'] >= 1


def test_stage_overview_reports_waiting_pre_and_post():
    uid = 970002
    create_session(uid, kind='work', source='auto', day='2026-04-16', slot='morning', anchor_id=1)
    sid = create_session(uid + 1, kind='home', source='auto', day='2026-04-16', slot='evening', anchor_id=2)
    assert set_pre(sid, 2)
    mark_audio_sent(sid)

    overview = get_messenger_stage_overview()

    assert overview['waiting_pre'] >= 1
    assert overview['waiting_post'] >= 1


def test_stage_overview_aggregates_funnel_by_slot():
    uid = 970100
    log_audio_timeline_event(uid, event_type='pre_score_received', sequence_key='full_series', platform='max', slot='morning')
    log_audio_timeline_event(uid, event_type='link_sent', sequence_key='full_series', platform='max', slot='morning')
    log_audio_timeline_event(uid, event_type='access_confirmed', sequence_key='full_series', platform='max', slot='morning')
    log_audio_timeline_event(uid, event_type='post_score_received', sequence_key='full_series', platform='max', slot='morning')
    log_audio_timeline_event(uid + 1, event_type='pre_score_received', sequence_key='full_series', platform='vk', slot='evening')
    log_audio_timeline_event(uid + 1, event_type='native_audio_sent', sequence_key='full_series', platform='vk', slot='evening')

    overview = get_messenger_stage_overview()

    assert overview['per_slot']['morning']['pre_score'] >= 1
    assert overview['per_slot']['morning']['audio_sent'] >= 1
    assert overview['per_slot']['morning']['confirmed'] >= 1
    assert overview['per_slot']['morning']['post_score'] >= 1
    assert overview['per_slot_platform']['morning']['max']['pre_score'] >= 1
    assert overview['per_slot']['evening']['pre_score'] >= 1
    assert overview['per_slot']['evening']['audio_sent'] >= 1
    assert overview['per_slot_platform']['evening']['vk']['audio_sent'] >= 1


def test_stage_overview_reports_waiting_by_slot():
    uid = 970200
    create_session(uid, kind='work', source='auto', day='2026-04-16', slot='morning', anchor_id=1)
    sid = create_session(uid + 1, kind='home', source='auto', day='2026-04-16', slot='evening', anchor_id=2)
    assert set_pre(sid, 1)
    mark_audio_sent(sid)

    overview = get_messenger_stage_overview()

    assert overview['waiting_pre_by_slot']['morning'] >= 1
    assert overview['waiting_post_by_slot']['evening'] >= 1


def test_policy_overview_reports_fallback_blocks_and_timezones():
    from services.events import log_event
    from services.db import db

    uid = 970300
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO user_delivery_preferences(user_id, timezone, quiet_hours_enabled, quiet_start, quiet_end, morning_channel, evening_channel, updated_at) VALUES(?,?,?,?,?,?,?,?)", (uid, 'Europe/Amsterdam', 1, '22:00', '08:00', 'max', 'telegram', '2026-04-16T00:00:00+00:00'))
        conn.execute("INSERT OR REPLACE INTO user_delivery_preferences(user_id, timezone, quiet_hours_enabled, quiet_start, quiet_end, morning_channel, evening_channel, updated_at) VALUES(?,?,?,?,?,?,?,?)", (uid + 1, 'Europe/Moscow', 0, None, None, 'telegram', 'max', '2026-04-16T00:00:00+00:00'))
    log_event(uid, 'auto_audio_channel_fallback', {'slot': 'morning', 'preferred': 'max', 'resolved': 'telegram', 'tz': 'Europe/Amsterdam'})
    log_event(uid, 'auto_audio_quiet_hours_block', {'slot': 'evening', 'tz': 'Europe/Amsterdam'})

    overview = get_messenger_policy_overview()

    assert overview['timezone_counts']['Europe/Amsterdam'] >= 1
    assert overview['timezone_counts']['Europe/Moscow'] >= 1
    assert overview['fallback_pairs']['max->telegram'] >= 1
    assert overview['fallback_by_slot']['morning'] >= 1
    assert overview['fallback_by_slot_platform']['morning']['max->telegram'] >= 1
    assert overview['blocked_by_slot']['evening'] >= 1
    assert overview['blocked_by_slot_platform']['evening']['telegram'] >= 1
    assert overview['blocked_by_timezone']['Europe/Amsterdam'] >= 1


def test_policy_overview_reports_slot_platform_timezone_breakdown():
    from services.events import log_event
    from services.db import db

    uid = 970301
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO user_delivery_preferences(user_id, timezone, quiet_hours_enabled, quiet_start, quiet_end, morning_channel, evening_channel, updated_at) VALUES(?,?,?,?,?,?,?,?)", (uid, 'Europe/Amsterdam', 1, '22:00', '08:00', 'max', 'telegram', '2026-04-16T00:00:00+00:00'))
        conn.execute("INSERT OR REPLACE INTO user_delivery_preferences(user_id, timezone, quiet_hours_enabled, quiet_start, quiet_end, morning_channel, evening_channel, updated_at) VALUES(?,?,?,?,?,?,?,?)", (uid + 1, 'Europe/Berlin', 1, '22:00', '08:00', 'vk', 'max', '2026-04-16T00:00:00+00:00'))
    log_event(uid, 'auto_audio_channel_fallback', {'slot': 'morning', 'preferred': 'max', 'resolved': 'telegram', 'tz': 'Europe/Amsterdam'})
    log_event(uid + 1, 'auto_audio_channel_fallback', {'slot': 'morning', 'preferred': 'max', 'resolved': 'telegram', 'tz': 'Europe/Berlin'})
    log_event(uid, 'auto_audio_quiet_hours_block', {'slot': 'evening', 'tz': 'Europe/Amsterdam'})

    overview = get_messenger_policy_overview()

    assert overview['fallback_by_slot_platform_timezone']['morning']['max->telegram']['Europe/Amsterdam'] >= 1
    assert overview['fallback_by_slot_platform_timezone']['morning']['max->telegram']['Europe/Berlin'] >= 1
    assert overview['blocked_by_slot_platform_timezone']['evening']['telegram']['Europe/Amsterdam'] >= 1
