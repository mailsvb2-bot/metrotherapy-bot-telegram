from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.settings import settings
from core.time_utils import utc_now
from services.audio_anchor import scan_full_anchored
from services.db import db, tx
from services.messenger.timeline import log_audio_timeline_event

SEQUENCE_FULL_SERIES = 'full_series'


@dataclass(frozen=True)
class AudioProgressItem:
    ordinal: int
    anchor: int
    title: str
    path: Path


@dataclass(frozen=True)
class AudioProgressSnapshot:
    user_id: int
    sequence_key: str
    last_anchor: int | None
    last_title: str | None
    last_platform: str | None
    last_confirmed_at: str | None
    pending_item: AudioProgressItem | None
    pending_platform: str | None
    pending_delivered_at: str | None
    next_item: AudioProgressItem | None


def _can_loop_audio(user_id: int) -> bool:
    return int(user_id) in set(settings.admin_id_list)


def list_full_series() -> list[AudioProgressItem]:
    items = scan_full_anchored()
    out: list[AudioProgressItem] = []
    for idx, item in enumerate(items, start=1):
        out.append(AudioProgressItem(ordinal=idx, anchor=int(item.anchor), title=str(item.clean_title), path=item.path))
    return out


def get_audio_item_by_anchor(anchor: int) -> AudioProgressItem | None:
    for item in list_full_series():
        if int(item.anchor) == int(anchor):
            return item
    return None


def get_last_progress(user_id: int, *, sequence_key: str = SEQUENCE_FULL_SERIES) -> dict[str, object | None]:
    with db() as conn:
        row = conn.execute(
            '''
            SELECT last_anchor, last_title, last_platform, delivered_at, updated_at, last_confirmed_at,
                   pending_anchor, pending_title, pending_path, pending_platform, pending_token, pending_delivered_at
            FROM user_audio_progress
            WHERE user_id=? AND sequence_key=?
            '''.strip(),
            (int(user_id), sequence_key),
        ).fetchone()
    if not row:
        return {
            'last_anchor': None,
            'last_title': None,
            'last_platform': None,
            'delivered_at': None,
            'updated_at': None,
            'last_confirmed_at': None,
            'pending_anchor': None,
            'pending_title': None,
            'pending_path': None,
            'pending_platform': None,
            'pending_token': None,
            'pending_delivered_at': None,
        }
    return dict(row)


def get_next_audio_item(user_id: int, *, sequence_key: str = SEQUENCE_FULL_SERIES) -> AudioProgressItem | None:
    items = list_full_series()
    if not items:
        return None
    last = get_last_progress(int(user_id), sequence_key=sequence_key)
    last_anchor = last.get('last_anchor')
    if last_anchor is None:
        return items[0]
    try:
        anchor = int(last_anchor)
    except (TypeError, ValueError):
        return items[0]
    for item in items:
        if item.anchor > anchor:
            return item
    return items[0] if _can_loop_audio(int(user_id)) else None


def record_audio_delivery(
    user_id: int,
    *,
    item: AudioProgressItem,
    platform: str,
    sequence_key: str = SEQUENCE_FULL_SERIES,
) -> None:
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        with tx(conn):
            conn.execute(
                '''
                INSERT INTO user_audio_progress(
                    user_id, sequence_key, last_anchor, last_title, last_path, last_platform, delivered_at,
                    updated_at, last_confirmed_at,
                    pending_anchor, pending_title, pending_path, pending_platform, pending_token, pending_delivered_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id, sequence_key) DO UPDATE SET
                    last_anchor=excluded.last_anchor,
                    last_title=excluded.last_title,
                    last_path=excluded.last_path,
                    last_platform=excluded.last_platform,
                    delivered_at=excluded.delivered_at,
                    updated_at=excluded.updated_at,
                    last_confirmed_at=excluded.last_confirmed_at,
                    pending_anchor=NULL,
                    pending_title=NULL,
                    pending_path=NULL,
                    pending_platform=NULL,
                    pending_token=NULL,
                    pending_delivered_at=NULL
                '''.strip(),
                (
                    int(user_id),
                    sequence_key,
                    int(item.anchor),
                    item.title,
                    str(item.path),
                    str(platform),
                    now,
                    now,
                    now,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
    log_audio_timeline_event(int(user_id), event_type="confirmed_delivery", sequence_key=sequence_key, anchor=int(item.anchor), title=item.title, platform=str(platform))


def mark_pending_audio_delivery(
    user_id: int,
    *,
    item: AudioProgressItem,
    platform: str,
    token: str | None,
    sequence_key: str = SEQUENCE_FULL_SERIES,
) -> None:
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        with tx(conn):
            conn.execute(
                '''
                INSERT INTO user_audio_progress(
                    user_id, sequence_key, last_anchor, last_title, last_path, last_platform, delivered_at,
                    updated_at, last_confirmed_at,
                    pending_anchor, pending_title, pending_path, pending_platform, pending_token, pending_delivered_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id, sequence_key) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    pending_anchor=excluded.pending_anchor,
                    pending_title=excluded.pending_title,
                    pending_path=excluded.pending_path,
                    pending_platform=excluded.pending_platform,
                    pending_token=excluded.pending_token,
                    pending_delivered_at=excluded.pending_delivered_at
                '''.strip(),
                (
                    int(user_id),
                    sequence_key,
                    None,
                    None,
                    None,
                    None,
                    None,
                    now,
                    None,
                    int(item.anchor),
                    item.title,
                    str(item.path),
                    str(platform),
                    str(token) if token is not None and str(token).strip() else None,
                    now,
                ),
            )


def get_pending_audio_item(user_id: int, *, sequence_key: str = SEQUENCE_FULL_SERIES) -> AudioProgressItem | None:
    last = get_last_progress(int(user_id), sequence_key=sequence_key)
    pending_anchor = last.get('pending_anchor')
    if pending_anchor is None:
        return None
    try:
        item = get_audio_item_by_anchor(int(pending_anchor))
        if item is not None:
            return item
    except (TypeError, ValueError):
        return None
    pending_path = last.get('pending_path')
    return AudioProgressItem(
        ordinal=0,
        anchor=int(pending_anchor),
        title=str(last.get('pending_title') or Path(str(pending_path or '')).stem or f'Audio {pending_anchor}'),
        path=Path(str(pending_path or '')),
    )


def get_pending_audio_token(user_id: int, *, sequence_key: str = SEQUENCE_FULL_SERIES) -> str | None:
    last = get_last_progress(int(user_id), sequence_key=sequence_key)
    token = (last.get('pending_token') or '')
    return str(token) if token else None




def confirm_pending_audio_delivery(
    user_id: int,
    *,
    platform: str | None = None,
    sequence_key: str = SEQUENCE_FULL_SERIES,
) -> AudioProgressItem | None:
    pending = get_pending_audio_item(int(user_id), sequence_key=sequence_key)
    if pending is None:
        return None
    last = get_last_progress(int(user_id), sequence_key=sequence_key)
    resolved_platform = str(platform or last.get("pending_platform") or last.get("last_platform") or "telegram")
    record_audio_delivery(int(user_id), item=pending, platform=resolved_platform, sequence_key=sequence_key)
    log_audio_timeline_event(
        int(user_id),
        event_type="manual_confirmed",
        sequence_key=sequence_key,
        anchor=int(pending.anchor),
        title=pending.title,
        platform=resolved_platform,
    )
    return pending

def get_progress_snapshot(user_id: int, *, sequence_key: str = SEQUENCE_FULL_SERIES) -> AudioProgressSnapshot:
    last = get_last_progress(int(user_id), sequence_key=sequence_key)
    pending_item = get_pending_audio_item(int(user_id), sequence_key=sequence_key)
    next_item = pending_item or get_next_audio_item(int(user_id), sequence_key=sequence_key)
    last_anchor = last.get('last_anchor')
    return AudioProgressSnapshot(
        user_id=int(user_id),
        sequence_key=sequence_key,
        last_anchor=int(last_anchor) if last_anchor is not None else None,
        last_title=str(last.get('last_title')) if last.get('last_title') is not None else None,
        last_platform=str(last.get('last_platform')) if last.get('last_platform') is not None else None,
        last_confirmed_at=str(last.get('last_confirmed_at')) if last.get('last_confirmed_at') is not None else None,
        pending_item=pending_item,
        pending_platform=str(last.get('pending_platform')) if last.get('pending_platform') is not None else None,
        pending_delivered_at=str(last.get('pending_delivered_at')) if last.get('pending_delivered_at') is not None else None,
        next_item=next_item,
    )
