from __future__ import annotations

import hashlib
import json
from typing import Any

from core.time_utils import utc_now
from services.db import db, tx
from services.messenger.platforms import normalize_platform


def _stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def register_inbound_event(platform: str, event_key: str | None, payload: dict[str, Any]) -> bool:
    key = (event_key or '').strip()
    if not key:
        key = _stable_hash(payload)
    norm = normalize_platform(platform)
    now = utc_now().replace(microsecond=0).isoformat()
    payload_hash = _stable_hash(payload)
    with db() as conn:
        with tx(conn):
            row = conn.execute(
                'SELECT 1 FROM messenger_webhook_events WHERE platform=? AND event_key=?',
                (norm, key),
            ).fetchone()
            if row is not None:
                return False
            conn.execute(
                '''
                INSERT INTO messenger_webhook_events(platform, event_key, received_at, payload_hash)
                VALUES(?,?,?,?)
                '''.strip(),
                (norm, key, now, payload_hash),
            )
    return True
