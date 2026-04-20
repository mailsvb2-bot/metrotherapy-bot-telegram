from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config.settings import settings
from core.time_utils import utc_now
from services.db import db, tx



def _safe_zoneinfo(value: str | None) -> ZoneInfo:
    user_tz = (value or '').strip()
    try:
        return ZoneInfo(user_tz)
    except (ValueError, ZoneInfoNotFoundError):
        return ZoneInfo('UTC')

VALID_PLATFORMS = {'telegram', 'max', 'vk'}


def _normalize_platform(value: str | None) -> str:
    raw = (value or '').strip().lower()
    return raw if raw in VALID_PLATFORMS else 'telegram'


def _parse_platform(value: str | None) -> str | None:
    raw = (value or '').strip().lower()
    return raw if raw in VALID_PLATFORMS else None


@dataclass(frozen=True)
class DeliveryPreferences:
    user_id: int
    timezone: str | None
    quiet_hours_enabled: bool
    quiet_start: str | None
    quiet_end: str | None
    morning_channel: str | None
    evening_channel: str | None
    updated_at: str | None


@dataclass(frozen=True)
class DeliveryPolicyDecision:
    user_id: int
    slot: str
    timezone: str
    preferred_channel: str
    resolved_channel: str
    fallback_used: bool
    blocked_by_quiet_hours: bool
    next_allowed_at: datetime | None


def _iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat()


def _normalize_hhmm(value: str | None) -> str | None:
    raw = (value or '').strip()
    if not raw:
        return None
    parts = raw.split(':')
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f'{hour:02d}:{minute:02d}'




def _resolved_timezone(value: str | None) -> str:
    raw = (value or '').strip() or (settings.TIMEZONE or 'UTC')
    try:
        ZoneInfo(raw)
        return raw
    except (ValueError, ZoneInfoNotFoundError):
        fallback = (settings.TIMEZONE or 'UTC').strip() or 'UTC'
        try:
            ZoneInfo(fallback)
            return fallback
        except (ValueError, ZoneInfoNotFoundError):
            return 'UTC'

def _validate_timezone(value: str | None) -> str | None:
    raw = (value or '').strip()
    if not raw:
        return None
    ZoneInfo(raw)
    return raw


def get_delivery_preferences(user_id: int) -> DeliveryPreferences:
    with db() as conn:
        row = conn.execute(
            '''
            SELECT user_id, timezone, quiet_hours_enabled, quiet_start, quiet_end,
                   morning_channel, evening_channel, updated_at
            FROM user_delivery_preferences
            WHERE user_id=?
            '''.strip(),
            (int(user_id),),
        ).fetchone()
    if not row:
        return DeliveryPreferences(int(user_id), None, False, None, None, None, None, None)
    return DeliveryPreferences(
        user_id=int(row['user_id']),
        timezone=row['timezone'],
        quiet_hours_enabled=bool(row['quiet_hours_enabled']),
        quiet_start=row['quiet_start'],
        quiet_end=row['quiet_end'],
        morning_channel=_normalize_platform(row['morning_channel']) if row['morning_channel'] else None,
        evening_channel=_normalize_platform(row['evening_channel']) if row['evening_channel'] else None,
        updated_at=row['updated_at'],
    )


def _upsert(user_id: int, **fields: object) -> None:
    current = get_delivery_preferences(int(user_id))
    payload = {
        'timezone': current.timezone,
        'quiet_hours_enabled': 1 if current.quiet_hours_enabled else 0,
        'quiet_start': current.quiet_start,
        'quiet_end': current.quiet_end,
        'morning_channel': current.morning_channel,
        'evening_channel': current.evening_channel,
        'updated_at': _iso_now(),
    }
    payload.update(fields)
    with db() as conn:
        with tx(conn):
            conn.execute(
                '''
                INSERT INTO user_delivery_preferences(
                    user_id, timezone, quiet_hours_enabled, quiet_start, quiet_end,
                    morning_channel, evening_channel, updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                    timezone=excluded.timezone,
                    quiet_hours_enabled=excluded.quiet_hours_enabled,
                    quiet_start=excluded.quiet_start,
                    quiet_end=excluded.quiet_end,
                    morning_channel=excluded.morning_channel,
                    evening_channel=excluded.evening_channel,
                    updated_at=excluded.updated_at
                '''.strip(),
                (
                    int(user_id),
                    payload['timezone'],
                    int(payload['quiet_hours_enabled']),
                    payload['quiet_start'],
                    payload['quiet_end'],
                    payload['morning_channel'],
                    payload['evening_channel'],
                    payload['updated_at'],
                ),
            )


def _get_common_preferred_platform(user_id: int) -> str:
    with db() as conn:
        row = conn.execute(
            'SELECT preferred_platform, last_seen_platform FROM user_channel_preferences WHERE user_id=?',
            (int(user_id),),
        ).fetchone()
    if not row:
        return 'telegram'
    return _normalize_platform(row['preferred_platform'] or row['last_seen_platform'])


def _get_available_platforms(user_id: int) -> list[str]:
    with db() as conn:
        rows = conn.execute(
            'SELECT platform FROM user_channel_identities WHERE user_id=? ORDER BY last_seen_at DESC',
            (int(user_id),),
        ).fetchall()
    out: list[str] = []
    for row in rows:
        platform = _normalize_platform(row['platform'])
        if platform not in out:
            out.append(platform)
    return out


def set_user_timezone(user_id: int, timezone_name: str) -> str:
    tz_name = _validate_timezone(timezone_name)
    if tz_name is None:
        raise ValueError('invalid timezone')
    _upsert(int(user_id), timezone=tz_name)
    return tz_name


def get_user_timezone(user_id: int) -> str | None:
    return get_delivery_preferences(int(user_id)).timezone


def set_quiet_hours(user_id: int, start_hhmm: str, end_hhmm: str) -> tuple[str, str]:
    start = _normalize_hhmm(start_hhmm)
    end = _normalize_hhmm(end_hhmm)
    if start is None or end is None:
        raise ValueError('invalid quiet hours')
    _upsert(int(user_id), quiet_hours_enabled=1, quiet_start=start, quiet_end=end)
    return start, end


def clear_quiet_hours(user_id: int) -> None:
    _upsert(int(user_id), quiet_hours_enabled=0, quiet_start=None, quiet_end=None)


def set_slot_channel(user_id: int, slot: str, platform: str | None) -> str | None:
    if slot not in {'morning', 'evening'}:
        raise ValueError('invalid slot')
    key = 'morning_channel' if slot == 'morning' else 'evening_channel'
    value = _parse_platform(platform) if platform else None
    if platform and value is None:
        raise ValueError('invalid platform')
    _upsert(int(user_id), **{key: value})
    return value


def get_slot_channel_preference(user_id: int, slot: str) -> str | None:
    prefs = get_delivery_preferences(int(user_id))
    if slot == 'morning':
        return prefs.morning_channel
    if slot == 'evening':
        return prefs.evening_channel
    raise ValueError('invalid slot')


def is_quiet_hours_now(user_id: int, *, now_utc: datetime | None = None) -> bool:
    prefs = get_delivery_preferences(int(user_id))
    if not prefs.quiet_hours_enabled or not prefs.quiet_start or not prefs.quiet_end:
        return False
    current_utc = now_utc or utc_now()
    tz_name = _resolved_timezone(prefs.timezone)
    local_dt = current_utc.astimezone(_safe_zoneinfo(tz_name))
    now_hm = local_dt.strftime('%H:%M')
    start = prefs.quiet_start
    end = prefs.quiet_end
    if start == end:
        return True
    if start < end:
        return start <= now_hm < end
    return now_hm >= start or now_hm < end


def next_allowed_send_at(user_id: int, *, now_utc: datetime | None = None) -> datetime | None:
    prefs = get_delivery_preferences(int(user_id))
    if not prefs.quiet_hours_enabled or not prefs.quiet_start or not prefs.quiet_end:
        return None
    current_utc = now_utc or utc_now()
    if not is_quiet_hours_now(int(user_id), now_utc=current_utc):
        return None
    tz_name = _resolved_timezone(prefs.timezone)
    local_dt = current_utc.astimezone(_safe_zoneinfo(tz_name))
    end_hour, end_minute = map(int, prefs.quiet_end.split(':'))
    target = local_dt.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
    if prefs.quiet_start >= prefs.quiet_end and local_dt.strftime('%H:%M') >= prefs.quiet_start:
        target += timedelta(days=1)
    return target.astimezone(current_utc.tzinfo or _safe_zoneinfo('UTC'))


def resolve_slot_channel(user_id: int, slot: str, *, fallback: str | None = None) -> str:
    preferred = get_slot_channel_preference(int(user_id), slot)
    available = _get_available_platforms(int(user_id))
    common = _normalize_platform(fallback) if fallback else _get_common_preferred_platform(int(user_id))
    if preferred and preferred in available:
        return preferred
    if common in available:
        return common
    if available:
        return available[0]
    return common


def build_delivery_policy_decision(user_id: int, slot: str, *, now_utc: datetime | None = None) -> DeliveryPolicyDecision:
    prefs = get_delivery_preferences(int(user_id))
    tz_name = _resolved_timezone(prefs.timezone)
    preferred = get_slot_channel_preference(int(user_id), slot) or _get_common_preferred_platform(int(user_id))
    resolved = resolve_slot_channel(int(user_id), slot)
    blocked = is_quiet_hours_now(int(user_id), now_utc=now_utc)
    return DeliveryPolicyDecision(
        user_id=int(user_id),
        slot=slot,
        timezone=tz_name,
        preferred_channel=_normalize_platform(preferred),
        resolved_channel=resolved,
        fallback_used=_normalize_platform(preferred) != resolved,
        blocked_by_quiet_hours=blocked,
        next_allowed_at=next_allowed_send_at(int(user_id), now_utc=now_utc) if blocked else None,
    )


def describe_delivery_preferences(user_id: int) -> str:
    prefs = get_delivery_preferences(int(user_id))
    timezone_name = _resolved_timezone(prefs.timezone)
    quiet = 'выключены'
    if prefs.quiet_hours_enabled and prefs.quiet_start and prefs.quiet_end:
        quiet = f'{prefs.quiet_start}–{prefs.quiet_end}'
    morning_channel = prefs.morning_channel or 'авто'
    evening_channel = prefs.evening_channel or 'авто'
    return (
        f'Часовой пояс: {timezone_name}\n'
        f'Тихие часы: {quiet}\n'
        f'Утренний канал: {morning_channel}\n'
        f'Вечерний канал: {evening_channel}'
    )
