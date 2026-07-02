from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.settings import settings
from core.time_utils import utc_now
from services.accounts.audio_progress import (
    DEFAULT_PRODUCT_ID,
    get_audio_state as get_account_audio_state,
    mark_audio_completed as mark_account_audio_completed,
    mark_audio_sent as mark_account_audio_sent,
)
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


def _empty_progress() -> dict[str, object | None]:
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


def _program_id(sequence_key: str) -> str:
    return str(sequence_key or SEQUENCE_FULL_SERIES)


def _canonical_account_id(user_id: int) -> int:
    uid = int(user_id)
    external = str(uid)
    with db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT account_id
            FROM account_channel_identities
            WHERE external_user_id=?
            ORDER BY account_id
            """.strip(),
            (external,),
        ).fetchall()
        account_ids = [int(row["account_id"]) for row in rows]
        if len(account_ids) == 1:
            return account_ids[0]

        row = conn.execute(
            "SELECT account_id FROM accounts WHERE account_id=? LIMIT 1",
            (uid,),
        ).fetchone()
        if row is not None:
            return int(row["account_id"])
    return uid


def _account_completion_platform(account_id: int, program_id: str, audio_no: int) -> str | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT source_platform AS platform
            FROM account_audio_completions
            WHERE account_id=? AND product_id=? AND program_id=? AND audio_no=?
            LIMIT 1
            """.strip(),
            (int(account_id), DEFAULT_PRODUCT_ID, str(program_id), int(audio_no)),
        ).fetchone()
    return str(row["platform"]) if row and row["platform"] is not None else None


def _account_delivery_platform(account_id: int, program_id: str, audio_no: int) -> str | None:
    with db() as conn:
        row = conn.execute(
            """
            SELECT platform
            FROM account_audio_deliveries
            WHERE account_id=? AND product_id=? AND program_id=? AND audio_no=?
            ORDER BY id DESC
            LIMIT 1
            """.strip(),
            (int(account_id), DEFAULT_PRODUCT_ID, str(program_id), int(audio_no)),
        ).fetchone()
    return str(row["platform"]) if row and row["platform"] is not None else None


def _overlay_account_progress(
    user_id: int,
    *,
    sequence_key: str,
    progress: dict[str, object | None],
) -> dict[str, object | None]:
    account_id = _canonical_account_id(int(user_id))
    program_id = _program_id(sequence_key)
    state = get_account_audio_state(account_id, program_id=program_id)
    out = dict(progress)

    try:
        legacy_last = int(out.get("last_anchor") or 0)
    except (TypeError, ValueError):
        legacy_last = 0

    if state.last_completed_audio_no > legacy_last:
        item = get_audio_item_by_anchor(state.last_completed_audio_no)
        platform = _account_completion_platform(account_id, program_id, state.last_completed_audio_no)
        out["last_anchor"] = state.last_completed_audio_no
        out["last_title"] = item.title if item is not None else out.get("last_title")
        out["last_platform"] = platform or out.get("last_platform")
        out["last_confirmed_at"] = state.updated_at
        out["updated_at"] = state.updated_at

    if state.pending_audio_no is not None and state.pending_audio_no > state.last_completed_audio_no:
        try:
            legacy_pending = int(out.get("pending_anchor") or 0)
        except (TypeError, ValueError):
            legacy_pending = 0
        if legacy_pending != state.pending_audio_no:
            item = get_audio_item_by_anchor(state.pending_audio_no)
            platform = _account_delivery_platform(account_id, program_id, state.pending_audio_no)
            out["pending_anchor"] = state.pending_audio_no
            out["pending_title"] = item.title if item is not None else out.get("pending_title")
            out["pending_path"] = str(item.path) if item is not None else out.get("pending_path")
            out["pending_platform"] = platform or out.get("pending_platform")
            out["pending_token"] = None
            out["pending_delivered_at"] = state.updated_at

    return out


def get_last_progress(user_id: int, *, sequence_key: str = SEQUENCE_FULL_SERIES) -> dict[str, object | None]:
    with db() as conn:
        row = conn.execute(
            """
            SELECT last_anchor, last_title, last_platform, delivered_at, updated_at, last_confirmed_at,
                   pending_anchor, pending_title, pending_path, pending_platform, pending_token, pending_delivered_at
            FROM user_audio_progress
            WHERE user_id=? AND sequence_key=?
            """.strip(),
            (int(user_id), sequence_key),
        ).fetchone()
    progress = dict(row) if row else _empty_progress()
    return _overlay_account_progress(int(user_id), sequence_key=sequence_key, progress=progress)


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
    account_id = _canonical_account_id(int(user_id))
    mark_account_audio_completed(
        account_id,
        int(item.anchor),
        platform=str(platform),
        program_id=_program_id(sequence_key),
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
    account_id = _canonical_account_id(int(user_id))
    mark_account_audio_sent(
        account_id,
        int(item.anchor),
        platform=str(platform),
        external_user_id=str(user_id),
        program_id=_program_id(sequence_key),
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
