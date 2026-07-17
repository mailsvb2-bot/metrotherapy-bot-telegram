from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from core.time_utils import utc_now
from services.db import db, tx
from services.messenger.audio_progress import (
    AudioProgressItem,
    SEQUENCE_FULL_SERIES,
    get_pending_audio_item,
    get_pending_audio_token,
    mark_pending_audio_delivery,
)
from services.messenger.timeline import log_audio_timeline_event


@dataclass(frozen=True)
class AudioAccessGrant:
    token: str
    user_id: int
    sequence_key: str
    anchor: int
    title: str | None
    file_path: Path
    platform: str
    created_at: str
    first_accessed_at: str | None
    access_count: int


def _audio_access_ttl_hours() -> int:
    raw = (os.getenv("AUDIO_ACCESS_TOKEN_TTL_HOURS") or "24").strip()
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return 24
    return max(1, min(parsed, 24 * 7))


def _grant_expired(created_at: str, *, now: datetime | None = None) -> bool:
    try:
        created = datetime.fromisoformat(str(created_at))
    except (TypeError, ValueError):
        return True
    current = now or utc_now()
    if created.tzinfo is None:
        created = created.replace(tzinfo=current.tzinfo)
    return current >= created + timedelta(hours=_audio_access_ttl_hours())


def issue_audio_access_token(
    user_id: int,
    *,
    item: AudioProgressItem,
    platform: str,
    sequence_key: str = SEQUENCE_FULL_SERIES,
) -> str:
    token = secrets.token_urlsafe(24)
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        with tx(conn):
            conn.execute(
                '''
                INSERT INTO user_audio_access_tokens(
                    token, user_id, sequence_key, anchor, title, file_path, platform, created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                '''.strip(),
                (
                    token,
                    int(user_id),
                    str(sequence_key),
                    int(item.anchor),
                    item.title,
                    str(item.path),
                    str(platform),
                    now,
                ),
            )
    mark_pending_audio_delivery(int(user_id), item=item, platform=platform, token=token, sequence_key=sequence_key)
    log_audio_timeline_event(
        int(user_id),
        event_type="issued_pending",
        sequence_key=sequence_key,
        anchor=int(item.anchor),
        title=item.title,
        platform=str(platform),
        token=token,
    )
    return token


def issue_or_reuse_audio_access_token(
    user_id: int,
    *,
    item: AudioProgressItem,
    platform: str,
    sequence_key: str = SEQUENCE_FULL_SERIES,
) -> str:
    pending_item = get_pending_audio_item(int(user_id), sequence_key=sequence_key)
    pending_token = get_pending_audio_token(int(user_id), sequence_key=sequence_key)
    if pending_item is not None and pending_token and int(pending_item.anchor) == int(item.anchor):
        grant = get_audio_access_grant(pending_token)
        if grant is not None and str(grant.platform) == str(platform):
            log_audio_timeline_event(
                int(user_id),
                event_type="reused_pending",
                sequence_key=sequence_key,
                anchor=int(item.anchor),
                title=item.title,
                platform=str(platform),
                token=pending_token,
            )
            return pending_token
    return issue_audio_access_token(int(user_id), item=item, platform=platform, sequence_key=sequence_key)


def get_audio_access_grant(token: str) -> AudioAccessGrant | None:
    raw = (token or "").strip()
    if not raw:
        return None
    with db() as conn:
        row = conn.execute(
            '''
            SELECT token, user_id, sequence_key, anchor, title, file_path, platform,
                   created_at, first_accessed_at, access_count
            FROM user_audio_access_tokens
            WHERE token=?
            '''.strip(),
            (raw,),
        ).fetchone()
    if not row:
        return None
    created_at = str(row["created_at"] or "")
    if _grant_expired(created_at):
        return None
    return AudioAccessGrant(
        token=str(row["token"]),
        user_id=int(row["user_id"]),
        sequence_key=str(row["sequence_key"]),
        anchor=int(row["anchor"]),
        title=str(row["title"]) if row["title"] is not None else None,
        file_path=Path(str(row["file_path"])),
        platform=str(row["platform"]),
        created_at=created_at,
        first_accessed_at=str(row["first_accessed_at"]) if row["first_accessed_at"] is not None else None,
        access_count=int(row["access_count"] or 0),
    )


def register_audio_access(token: str) -> AudioAccessGrant | None:
    """Record that the protected file URL was fetched without completing progress.

    Link-preview bots, antivirus scanners and proxies can issue GET requests without
    the user listening to the audio. Completion therefore remains an explicit user
    action handled by ``confirm_pending_audio_delivery``.
    """

    grant = get_audio_access_grant(token)
    if grant is None:
        return None
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        with tx(conn):
            conn.execute(
                '''
                UPDATE user_audio_access_tokens
                SET first_accessed_at=COALESCE(first_accessed_at, ?),
                    last_accessed_at=?,
                    access_count=access_count + 1
                WHERE token=?
                '''.strip(),
                (now, now, grant.token),
            )
    log_audio_timeline_event(
        int(grant.user_id),
        event_type="accessed",
        sequence_key=grant.sequence_key,
        anchor=int(grant.anchor),
        title=grant.title or grant.file_path.stem,
        platform=grant.platform,
        token=grant.token,
    )
    return get_audio_access_grant(token)
