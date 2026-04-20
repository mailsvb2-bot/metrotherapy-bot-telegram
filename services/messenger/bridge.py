from __future__ import annotations

import secrets
from dataclasses import dataclass

from datetime import datetime, timedelta

from config.settings import settings
from core.time_utils import utc_now
from services.db import db, tx


@dataclass(frozen=True)
class BridgeResolution:
    canonical_user_id: int
    token: str
    consumed: bool


PURPOSE_SWITCH = 'switch_messenger'


def issue_bridge_token(user_id: int, *, purpose: str = PURPOSE_SWITCH) -> str:
    token = secrets.token_urlsafe(18)
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        with tx(conn):
            conn.execute(
                '''
                INSERT INTO user_channel_bridge_tokens(token, user_id, purpose, created_at)
                VALUES(?,?,?,?)
                '''.strip(),
                (token, int(user_id), str(purpose), now),
            )
    return token


def resolve_bridge_token(token: str) -> BridgeResolution | None:
    raw = (token or '').strip()
    if not raw:
        return None
    with db() as conn:
        row = conn.execute(
            '''
            SELECT token, user_id, used_at, created_at
            FROM user_channel_bridge_tokens
            WHERE token=? AND purpose=?
            '''.strip(),
            (raw, PURPOSE_SWITCH),
        ).fetchone()
    if not row:
        return None
    created_at = row['created_at']
    ttl_hours = int(getattr(settings, 'MESSENGER_BRIDGE_TOKEN_TTL_HOURS', 72) or 72)
    if created_at:
        try:
            created = datetime.fromisoformat(str(created_at))
            if created + timedelta(hours=ttl_hours) < utc_now():
                return None
        except (ValueError, TypeError):
            pass
    return BridgeResolution(
        canonical_user_id=int(row['user_id']),
        token=str(row['token']),
        consumed=bool(row['used_at']),
    )


def consume_bridge_token(token: str, *, platform: str, external_user_id: str | None) -> BridgeResolution | None:
    resolved = resolve_bridge_token(token)
    if resolved is None:
        return None
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        with tx(conn):
            conn.execute(
                '''
                UPDATE user_channel_bridge_tokens
                SET used_at=COALESCE(used_at, ?),
                    used_platform=COALESCE(used_platform, ?),
                    used_external_user_id=COALESCE(used_external_user_id, ?)
                WHERE token=?
                '''.strip(),
                (now, str(platform), (external_user_id or '').strip() or None, token),
            )
    return BridgeResolution(canonical_user_id=resolved.canonical_user_id, token=resolved.token, consumed=True)
