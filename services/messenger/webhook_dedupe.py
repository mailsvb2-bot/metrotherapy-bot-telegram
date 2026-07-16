from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from core.time_utils import utc_now
from services.db import db, tx
from services.db.runtime import CONFIG
from services.messenger.platforms import normalize_platform


def _stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _event_key(event_key: str | None, payload: dict[str, Any]) -> str:
    return (event_key or "").strip() or _stable_hash(payload)


def _row_value(row: Any, key: str, index: int) -> Any:
    if hasattr(row, "keys"):
        return row[key]
    return row[index]


def claim_inbound_event(
    platform: str,
    event_key: str | None,
    payload: dict[str, Any],
    *,
    stale_after_sec: int = 120,
) -> bool:
    """Claim one provider event for processing.

    Completed events are permanent duplicates. Failed events and stale processing
    claims may be retried. A Postgres advisory transaction lock serializes claims
    for the same provider key across processes; SQLite uses its write transaction.
    """

    key = _event_key(event_key, payload)
    norm = normalize_platform(platform)
    now = utc_now().replace(microsecond=0)
    now_iso = now.isoformat()
    stale_before = (now - timedelta(seconds=max(1, int(stale_after_sec)))).isoformat()
    payload_hash = _stable_hash(payload)

    with db() as conn:
        with tx(conn):
            if CONFIG.uses_postgres:
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtext(?)::bigint)",
                    (f"messenger-inbound:{norm}:{key}",),
                )
            row = conn.execute(
                """
                SELECT status, updated_at, attempts
                FROM messenger_webhook_events
                WHERE platform=? AND event_key=?
                LIMIT 1
                """.strip(),
                (norm, key),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO messenger_webhook_events(
                        platform, event_key, received_at, payload_hash,
                        status, attempts, updated_at, completed_at, last_error
                    ) VALUES(?,?,?,?, 'processing',1,?,NULL,'')
                    """.strip(),
                    (norm, key, now_iso, payload_hash, now_iso),
                )
                return True

            status = str(_row_value(row, "status", 0) or "completed").strip().lower()
            updated_at = str(_row_value(row, "updated_at", 1) or "")
            attempts = int(_row_value(row, "attempts", 2) or 0)
            if status == "completed":
                return False
            if status == "processing" and updated_at and updated_at > stale_before:
                return False

            conn.execute(
                """
                UPDATE messenger_webhook_events
                SET status='processing', attempts=?, updated_at=?, payload_hash=?, last_error=''
                WHERE platform=? AND event_key=?
                """.strip(),
                (attempts + 1, now_iso, payload_hash, norm, key),
            )
            return True


def complete_inbound_event(platform: str, event_key: str | None, payload: dict[str, Any]) -> None:
    key = _event_key(event_key, payload)
    norm = normalize_platform(platform)
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                UPDATE messenger_webhook_events
                SET status='completed', completed_at=?, updated_at=?, last_error=''
                WHERE platform=? AND event_key=?
                """.strip(),
                (now, now, norm, key),
            )


def fail_inbound_event(platform: str, event_key: str | None, payload: dict[str, Any], error: str) -> None:
    key = _event_key(event_key, payload)
    norm = normalize_platform(platform)
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                UPDATE messenger_webhook_events
                SET status='failed', updated_at=?, last_error=?
                WHERE platform=? AND event_key=?
                """.strip(),
                (now, str(error or "")[:500], norm, key),
            )


def register_inbound_event(platform: str, event_key: str | None, payload: dict[str, Any]) -> bool:
    """Compatibility API: claim and immediately complete a dedupe-only event."""

    claimed = claim_inbound_event(platform, event_key, payload)
    if claimed:
        complete_inbound_event(platform, event_key, payload)
    return claimed
