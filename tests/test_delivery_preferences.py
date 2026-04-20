from __future__ import annotations

from datetime import datetime, timezone

from services.db import db
from services.migrations import apply_all_migrations
from services.delivery_preferences import (
    get_delivery_preferences,
    set_user_timezone,
    set_quiet_hours,
    clear_quiet_hours,
    is_quiet_hours_now,
    set_slot_channel,
    resolve_slot_channel,
    next_allowed_send_at,
    build_delivery_policy_decision,
)
from services.messenger.preferences import record_channel_identity, set_preferred_platform


def _migrate() -> None:
    with db() as conn:
        apply_all_migrations(conn)


def test_timezone_roundtrip():
    _migrate()
    tz_name = set_user_timezone(101, 'Europe/Amsterdam')
    prefs = get_delivery_preferences(101)
    assert tz_name == 'Europe/Amsterdam'
    assert prefs.timezone == 'Europe/Amsterdam'


def test_quiet_hours_cross_midnight():
    _migrate()
    set_user_timezone(102, 'UTC')
    set_quiet_hours(102, '22:00', '08:00')
    assert is_quiet_hours_now(102, now_utc=datetime(2026, 4, 16, 23, 30, tzinfo=timezone.utc)) is True
    assert is_quiet_hours_now(102, now_utc=datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)) is False


def test_clear_quiet_hours():
    _migrate()
    set_quiet_hours(103, '22:00', '08:00')
    clear_quiet_hours(103)
    prefs = get_delivery_preferences(103)
    assert prefs.quiet_hours_enabled is False
    assert prefs.quiet_start is None
    assert prefs.quiet_end is None


def test_slot_channel_resolution_prefers_connected_slot_channel():
    _migrate()
    user_id = 104
    record_channel_identity(user_id, 'telegram', '104')
    record_channel_identity(user_id, 'max', 'mx104')
    set_preferred_platform(user_id, 'telegram')
    set_slot_channel(user_id, 'morning', 'max')
    assert resolve_slot_channel(user_id, 'morning') == 'max'


def test_slot_channel_resolution_falls_back_to_general_preference():
    _migrate()
    user_id = 105
    record_channel_identity(user_id, 'telegram', '105')
    set_preferred_platform(user_id, 'telegram')
    set_slot_channel(user_id, 'morning', 'vk')
    assert resolve_slot_channel(user_id, 'morning') == 'telegram'


def test_next_allowed_send_at_after_cross_midnight_quiet_hours():
    _migrate()
    user_id = 106
    set_user_timezone(user_id, 'UTC')
    set_quiet_hours(user_id, '22:00', '08:00')
    result = next_allowed_send_at(user_id, now_utc=datetime(2026, 4, 16, 23, 30, tzinfo=timezone.utc))
    assert result == datetime(2026, 4, 17, 8, 0, tzinfo=timezone.utc)


def test_delivery_policy_decision_marks_fallback_and_quiet_hours():
    _migrate()
    user_id = 107
    record_channel_identity(user_id, 'telegram', '107')
    set_user_timezone(user_id, 'UTC')
    set_quiet_hours(user_id, '22:00', '08:00')
    set_slot_channel(user_id, 'evening', 'vk')
    decision = build_delivery_policy_decision(user_id, 'evening', now_utc=datetime(2026, 4, 16, 23, 15, tzinfo=timezone.utc))
    assert decision.preferred_channel == 'vk'
    assert decision.resolved_channel == 'telegram'
    assert decision.fallback_used is True
    assert decision.blocked_by_quiet_hours is True
    assert decision.next_allowed_at == datetime(2026, 4, 17, 8, 0, tzinfo=timezone.utc)
