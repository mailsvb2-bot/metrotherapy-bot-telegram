from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json

from core.time_utils import utc_now
from services.db import db, tx


@dataclass(frozen=True)
class AudioTimelineEvent:
    id: int
    user_id: int
    sequence_key: str
    event_type: str
    anchor: int | None
    title: str | None
    platform: str | None
    token: str | None
    meta_json: str | None
    created_at: str


def log_audio_timeline_event(
    user_id: int,
    *,
    event_type: str,
    sequence_key: str,
    anchor: int | None = None,
    title: str | None = None,
    platform: str | None = None,
    token: str | None = None,
    meta_json: str | None = None,
    slot: str | None = None,
) -> None:
    now = utc_now().replace(microsecond=0).isoformat()
    payload: dict[str, Any] = {}
    if meta_json:
        try:
            parsed = json.loads(str(meta_json))
            if isinstance(parsed, dict):
                payload.update(parsed)
            else:
                payload['value'] = parsed
        except (json.JSONDecodeError, TypeError):
            payload['raw_meta'] = str(meta_json)
    if slot:
        payload['slot'] = str(slot)
    normalized_meta = json.dumps(payload, ensure_ascii=False) if payload else None
    with db() as conn:
        with tx(conn):
            conn.execute(
                '''
                INSERT INTO user_audio_timeline(
                    user_id, sequence_key, event_type, anchor, title, platform, token, meta_json, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                '''.strip(),
                (
                    int(user_id),
                    str(sequence_key),
                    str(event_type),
                    int(anchor) if anchor is not None else None,
                    title,
                    platform,
                    token,
                    normalized_meta,
                    now,
                ),
            )


def get_recent_audio_timeline(user_id: int, *, sequence_key: str, limit: int = 10) -> list[AudioTimelineEvent]:
    cap = max(1, min(int(limit), 50))
    with db() as conn:
        rows = conn.execute(
            '''
            SELECT id, user_id, sequence_key, event_type, anchor, title, platform, token, meta_json, created_at
            FROM user_audio_timeline
            WHERE user_id=? AND sequence_key=?
            ORDER BY id DESC
            LIMIT ?
            '''.strip(),
            (int(user_id), str(sequence_key), cap),
        ).fetchall()
    return [
        AudioTimelineEvent(
            id=int(row['id']),
            user_id=int(row['user_id']),
            sequence_key=str(row['sequence_key']),
            event_type=str(row['event_type']),
            anchor=int(row['anchor']) if row['anchor'] is not None else None,
            title=str(row['title']) if row['title'] is not None else None,
            platform=str(row['platform']) if row['platform'] is not None else None,
            token=str(row['token']) if row['token'] is not None else None,
            meta_json=str(row['meta_json']) if row['meta_json'] is not None else None,
            created_at=str(row['created_at']),
        )
        for row in rows
    ]


def get_messenger_runtime_overview() -> dict[str, Any]:
    with db() as conn:
        total_profiles = int(conn.execute('SELECT COUNT(DISTINCT user_id) FROM user_channel_preferences').fetchone()[0] or 0)
        linked_identities = int(conn.execute('SELECT COUNT(*) FROM user_channel_identities').fetchone()[0] or 0)
        bridge_links = int(conn.execute('SELECT COUNT(*) FROM user_channel_bridge_tokens WHERE used_at IS NOT NULL').fetchone()[0] or 0)
        pending_audio = int(conn.execute('SELECT COUNT(*) FROM user_audio_progress WHERE pending_anchor IS NOT NULL').fetchone()[0] or 0)
        confirmed_audio = int(conn.execute('SELECT COUNT(*) FROM user_audio_progress WHERE last_anchor IS NOT NULL').fetchone()[0] or 0)
        webhook_events = int(conn.execute('SELECT COUNT(*) FROM messenger_webhook_events').fetchone()[0] or 0)
        audio_accesses = int(conn.execute('SELECT COUNT(*) FROM user_audio_access_tokens WHERE first_accessed_at IS NOT NULL').fetchone()[0] or 0)
        platform_rows = conn.execute(
            '''
            SELECT platform, COUNT(*) AS cnt
            FROM user_channel_identities
            GROUP BY platform
            ORDER BY platform
            '''.strip()
        ).fetchall()
    return {
        'total_profiles': total_profiles,
        'linked_identities': linked_identities,
        'bridge_links': bridge_links,
        'pending_audio': pending_audio,
        'confirmed_audio': confirmed_audio,
        'webhook_events': webhook_events,
        'audio_accesses': audio_accesses,
        'platform_counts': {str(row['platform']): int(row['cnt'] or 0) for row in platform_rows},
    }


def get_messenger_stage_overview() -> dict[str, Any]:
    tracked = (
        'pre_score_received',
        'telegram_sent',
        'native_audio_sent',
        'link_sent',
        'manual_confirmed',
        'access_confirmed',
        'post_score_received',
    )
    with db() as conn:
        rows = conn.execute(
            '''
            SELECT COALESCE(platform, 'unknown') AS platform, event_type, meta_json
            FROM user_audio_timeline
            WHERE event_type IN (?,?,?,?,?,?,?)
            ORDER BY id
            '''.strip(),
            tracked,
        ).fetchall()
        waiting_rows = conn.execute(
            '''
            SELECT slot, pre_score, post_score, audio_sent
            FROM mood_sessions
            WHERE COALESCE(source,'') IN ('auto','settings')
              AND COALESCE(kind,'') IN ('work','home')
              AND COALESCE(slot,'') IN ('morning','evening')
            '''.strip()
        ).fetchall()

    def _empty_bucket() -> dict[str, int]:
        return {'pre_score': 0, 'audio_sent': 0, 'confirmed': 0, 'post_score': 0}

    def _extract_slot(meta_json: str | None) -> str | None:
        if not meta_json:
            return None
        try:
            payload = json.loads(str(meta_json))
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        slot = payload.get('slot')
        if slot in {'morning', 'evening'}:
            return str(slot)
        kind = payload.get('kind')
        if kind == 'work':
            return 'morning'
        if kind == 'home':
            return 'evening'
        return None

    per_platform: dict[str, dict[str, int]] = {}
    per_slot: dict[str, dict[str, int]] = {'morning': _empty_bucket(), 'evening': _empty_bucket()}
    per_slot_platform: dict[str, dict[str, dict[str, int]]] = {'morning': {}, 'evening': {}}

    for row in rows:
        platform = str(row['platform'])
        event_type = str(row['event_type'])
        slot = _extract_slot(row['meta_json'])
        bucket = per_platform.setdefault(platform, _empty_bucket())
        slot_bucket = per_slot.get(slot) if slot in per_slot else None
        slot_platform_bucket = None
        if slot_bucket is not None:
            slot_platform_bucket = per_slot_platform[slot].setdefault(platform, _empty_bucket())

        targets = [bucket]
        if slot_bucket is not None:
            targets.append(slot_bucket)
        if slot_platform_bucket is not None:
            targets.append(slot_platform_bucket)

        for target in targets:
            if event_type == 'pre_score_received':
                target['pre_score'] += 1
            elif event_type in {'telegram_sent', 'native_audio_sent', 'link_sent'}:
                target['audio_sent'] += 1
            elif event_type in {'manual_confirmed', 'access_confirmed'}:
                target['confirmed'] += 1
            elif event_type == 'post_score_received':
                target['post_score'] += 1

    waiting_pre = 0
    waiting_post = 0
    waiting_pre_by_slot = {'morning': 0, 'evening': 0}
    waiting_post_by_slot = {'morning': 0, 'evening': 0}
    for row in waiting_rows:
        slot = str(row['slot'])
        pre_score = row['pre_score']
        post_score = row['post_score']
        audio_sent = int(row['audio_sent'] or 0)
        if pre_score is None and audio_sent == 0:
            waiting_pre += 1
            waiting_pre_by_slot[slot] += 1
        if pre_score is not None and post_score is None and audio_sent == 1:
            waiting_post += 1
            waiting_post_by_slot[slot] += 1

    return {
        'per_platform': per_platform,
        'per_slot': per_slot,
        'per_slot_platform': per_slot_platform,
        'waiting_pre': waiting_pre,
        'waiting_post': waiting_post,
        'waiting_pre_by_slot': waiting_pre_by_slot,
        'waiting_post_by_slot': waiting_post_by_slot,
    }


def get_messenger_policy_overview() -> dict[str, Any]:
    with db() as conn:
        pref_rows = conn.execute(
            (
                "SELECT timezone, COUNT(*) AS cnt "
                "FROM user_delivery_preferences "
                "GROUP BY timezone "
                "ORDER BY cnt DESC, timezone"
            )
        ).fetchall()
        event_rows = conn.execute(
            (
                "SELECT user_id, name, meta "
                "FROM events "
                "WHERE name IN ('auto_audio_channel_fallback', 'auto_audio_quiet_hours_block') "
                "ORDER BY id"
            )
        ).fetchall()

    timezone_counts: dict[str, int] = {}
    for row in pref_rows:
        timezone_name = str(row['timezone'] or 'default')
        timezone_counts[timezone_name] = int(row['cnt'] or 0)

    fallback_pairs: dict[str, int] = {}
    fallback_by_slot = {'morning': 0, 'evening': 0}
    fallback_by_slot_platform: dict[str, dict[str, int]] = {'morning': {}, 'evening': {}}
    fallback_by_slot_platform_timezone: dict[str, dict[str, dict[str, int]]] = {'morning': {}, 'evening': {}}
    blocked_by_slot = {'morning': 0, 'evening': 0}
    blocked_by_slot_platform: dict[str, dict[str, int]] = {'morning': {}, 'evening': {}}
    blocked_by_slot_platform_timezone: dict[str, dict[str, dict[str, int]]] = {'morning': {}, 'evening': {}}
    blocked_by_timezone: dict[str, int] = {}

    for row in event_rows:
        name = str(row['name'])
        try:
            payload = json.loads(str(row['meta'] or '{}'))
        except (json.JSONDecodeError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        slot = str(payload.get('slot') or '')
        if slot not in {'morning', 'evening'}:
            kind = payload.get('kind')
            slot = 'morning' if kind == 'work' else 'evening' if kind == 'home' else ''
        timezone_name = str(payload.get('tz') or payload.get('timezone') or 'default')
        preferred = str(payload.get('preferred') or 'unknown')
        resolved = str(payload.get('resolved') or 'unknown')
        user_id = int(row['user_id'] or 0) if row['user_id'] is not None else 0
        if user_id and (preferred == 'unknown' or resolved == 'unknown'):
            pref_row = None
            with db() as conn:
                pref_row = conn.execute(
                    'SELECT morning_channel, evening_channel FROM user_delivery_preferences WHERE user_id=?',
                    (user_id,),
                ).fetchone()
            if pref_row is not None:
                fallback_channel = str(pref_row['morning_channel'] if slot == 'morning' else pref_row['evening_channel'] if slot == 'evening' else '') or 'auto'
                if preferred == 'unknown':
                    preferred = fallback_channel
                if resolved == 'unknown':
                    resolved = fallback_channel
        if name == 'auto_audio_channel_fallback':
            pair = f'{preferred}->{resolved}'
            fallback_pairs[pair] = fallback_pairs.get(pair, 0) + 1
            if slot in fallback_by_slot:
                fallback_by_slot[slot] += 1
                slot_bucket = fallback_by_slot_platform[slot]
                slot_bucket[pair] = slot_bucket.get(pair, 0) + 1
                tz_bucket = fallback_by_slot_platform_timezone[slot].setdefault(pair, {})
                tz_bucket[timezone_name] = tz_bucket.get(timezone_name, 0) + 1
        elif name == 'auto_audio_quiet_hours_block':
            if slot in blocked_by_slot:
                blocked_by_slot[slot] += 1
                platform = resolved if resolved != 'unknown' else preferred
                slot_bucket = blocked_by_slot_platform[slot]
                slot_bucket[platform] = slot_bucket.get(platform, 0) + 1
                tz_bucket = blocked_by_slot_platform_timezone[slot].setdefault(platform, {})
                tz_bucket[timezone_name] = tz_bucket.get(timezone_name, 0) + 1
            blocked_by_timezone[timezone_name] = blocked_by_timezone.get(timezone_name, 0) + 1

    return {
        'timezone_counts': timezone_counts,
        'fallback_pairs': fallback_pairs,
        'fallback_by_slot': fallback_by_slot,
        'fallback_by_slot_platform': fallback_by_slot_platform,
        'fallback_by_slot_platform_timezone': fallback_by_slot_platform_timezone,
        'blocked_by_slot': blocked_by_slot,
        'blocked_by_slot_platform': blocked_by_slot_platform,
        'blocked_by_slot_platform_timezone': blocked_by_slot_platform_timezone,
        'blocked_by_timezone': blocked_by_timezone,
    }
