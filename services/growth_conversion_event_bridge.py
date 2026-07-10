from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.db import db, tx
from services.growth_conversion_event_bridge_core import (
    map_event_to_conversion,
    parse_event_meta,
    supported_event_names,
)
from services.growth_conversion_hub import enqueue_conversion_dry_run_tx
from services.migrations._helpers import table_exists

log = logging.getLogger(__name__)

BRIDGE_NAME = "events_to_growth_conversions_v1"


@dataclass(frozen=True)
class EventBridgeResult:
    processed: int = 0
    inserted: int = 0
    duplicates: int = 0
    last_event_id: int = 0
    error: str = ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _rowdict(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return {}


def ensure_bridge_schema(conn: Any) -> None:
    if not table_exists(conn, "events"):
        raise RuntimeError("events_schema_not_migrated")
    if not table_exists(conn, "growth_conversion_outbox"):
        raise RuntimeError("growth_conversion_outbox_schema_not_migrated")
    if not table_exists(conn, "growth_conversion_bridge_state"):
        raise RuntimeError("growth_conversion_bridge_state_schema_not_migrated")


def _last_event_id(conn: Any) -> int:
    row = conn.execute(
        "SELECT last_event_id FROM growth_conversion_bridge_state WHERE bridge_name=? LIMIT 1",
        (BRIDGE_NAME,),
    ).fetchone()
    data = _rowdict(row)
    try:
        return max(0, int(data.get("last_event_id") or 0))
    except (TypeError, ValueError):
        return 0


def _event_rows(conn: Any, *, after_id: int, batch_size: int) -> list[dict[str, Any]]:
    names = supported_event_names()
    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"""
        SELECT id, user_id, name, event, meta, created_at, ts
        FROM events
        WHERE id > ?
          AND COALESCE(NULLIF(name, ''), event, '') IN ({placeholders})
        ORDER BY id ASC
        LIMIT ?
        """.strip(),
        tuple([int(after_id), *names, int(batch_size)]),
    ).fetchall()
    return [_rowdict(row) for row in rows]


def _latest_start_attribution(conn: Any, *, user_id: int, event_id: int) -> dict[str, Any]:
    if int(user_id) <= 0:
        return {}
    row = conn.execute(
        """
        SELECT meta
        FROM events
        WHERE user_id=?
          AND id<=?
          AND COALESCE(NULLIF(name, ''), event, '')='funnel_start_command'
        ORDER BY id DESC
        LIMIT 1
        """.strip(),
        (int(user_id), int(event_id)),
    ).fetchone()
    return parse_event_meta(_rowdict(row).get("meta"))


def _save_state(
    conn: Any,
    *,
    last_event_id: int,
    batch_size: int,
    inserted: int,
    duplicates: int,
    last_error: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO growth_conversion_bridge_state(
            bridge_name, last_event_id, last_batch_size, last_inserted,
            last_duplicates, last_error, updated_at
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(bridge_name) DO UPDATE SET
            last_event_id=excluded.last_event_id,
            last_batch_size=excluded.last_batch_size,
            last_inserted=excluded.last_inserted,
            last_duplicates=excluded.last_duplicates,
            last_error=excluded.last_error,
            updated_at=excluded.updated_at
        """.strip(),
        (
            BRIDGE_NAME,
            int(last_event_id),
            int(batch_size),
            int(inserted),
            int(duplicates),
            str(last_error or "")[:500],
            _utc_now_iso(),
        ),
    )


def run_event_conversion_bridge_once(*, batch_size: int = 100) -> EventBridgeResult:
    size = max(1, min(int(batch_size), 500))
    with db() as conn:
        ensure_bridge_schema(conn)
        with tx(conn):
            cursor = _last_event_id(conn)
            rows = _event_rows(conn, after_id=cursor, batch_size=size)
            if not rows:
                return EventBridgeResult(last_event_id=cursor)

            inserted = 0
            duplicates = 0
            last_event_id = cursor
            for row in rows:
                event_id = int(row.get("id") or 0)
                attribution = _latest_start_attribution(
                    conn,
                    user_id=int(row.get("user_id") or 0),
                    event_id=event_id,
                )
                mapped = map_event_to_conversion(row, attribution=attribution)
                if mapped is None:
                    continue
                result = enqueue_conversion_dry_run_tx(conn, **mapped)
                if result.inserted:
                    inserted += 1
                else:
                    duplicates += 1
                last_event_id = max(last_event_id, event_id)

            _save_state(
                conn,
                last_event_id=last_event_id,
                batch_size=len(rows),
                inserted=inserted,
                duplicates=duplicates,
            )
            return EventBridgeResult(
                processed=len(rows),
                inserted=inserted,
                duplicates=duplicates,
                last_event_id=last_event_id,
            )


def run_event_conversion_bridge_safe(*, batch_size: int = 100) -> EventBridgeResult:
    try:
        return run_event_conversion_bridge_once(batch_size=batch_size)
    except sqlite3.Error as exc:
        log.warning("Growth event conversion bridge skipped: %s", type(exc).__name__)
        return EventBridgeResult(error=f"{type(exc).__name__}:{exc}")
    except OSError as exc:
        log.warning("Growth event conversion bridge skipped: %s", type(exc).__name__)
        return EventBridgeResult(error=f"{type(exc).__name__}:{exc}")
    except RuntimeError as exc:
        log.warning("Growth event conversion bridge skipped: %s", type(exc).__name__)
        return EventBridgeResult(error=f"{type(exc).__name__}:{exc}")
    except (TypeError, ValueError) as exc:
        log.warning("Growth event conversion bridge skipped: %s", type(exc).__name__)
        return EventBridgeResult(error=f"{type(exc).__name__}:{exc}")


def event_conversion_bridge_snapshot() -> dict[str, Any]:
    try:
        with db() as conn:
            ensure_bridge_schema(conn)
            row = conn.execute(
                """
                SELECT bridge_name, last_event_id, last_batch_size, last_inserted,
                       last_duplicates, last_error, updated_at
                FROM growth_conversion_bridge_state
                WHERE bridge_name=?
                LIMIT 1
                """.strip(),
                (BRIDGE_NAME,),
            ).fetchone()
        data = _rowdict(row)
        return {
            "ok": True,
            "bridge_name": BRIDGE_NAME,
            "last_event_id": int(data.get("last_event_id") or 0),
            "last_batch_size": int(data.get("last_batch_size") or 0),
            "last_inserted": int(data.get("last_inserted") or 0),
            "last_duplicates": int(data.get("last_duplicates") or 0),
            "last_error": str(data.get("last_error") or ""),
            "updated_at": str(data.get("updated_at") or ""),
        }
    except sqlite3.Error as exc:
        return {"ok": False, "bridge_name": BRIDGE_NAME, "error": f"{type(exc).__name__}:{exc}"}
    except OSError as exc:
        return {"ok": False, "bridge_name": BRIDGE_NAME, "error": f"{type(exc).__name__}:{exc}"}
    except RuntimeError as exc:
        return {"ok": False, "bridge_name": BRIDGE_NAME, "error": f"{type(exc).__name__}:{exc}"}
    except (TypeError, ValueError) as exc:
        return {"ok": False, "bridge_name": BRIDGE_NAME, "error": f"{type(exc).__name__}:{exc}"}
