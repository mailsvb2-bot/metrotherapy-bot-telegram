from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "messenger_delivery_outbox_v1"
log = logging.getLogger(__name__)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608 - internal constant table names only
    return {str(row["name"] if hasattr(row, "keys") else row[1]) for row in rows}


def _ensure_inbound_state_columns(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "messenger_webhook_events")
    additions = {
        "status": "TEXT NOT NULL DEFAULT 'completed'",
        "attempts": "INTEGER NOT NULL DEFAULT 1",
        "updated_at": "TEXT",
        "completed_at": "TEXT",
        "last_error": "TEXT NOT NULL DEFAULT ''",
    }
    for name, ddl in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE messenger_webhook_events ADD COLUMN {name} {ddl}")  # nosec B608
    conn.execute(
        "UPDATE messenger_webhook_events "
        "SET status=COALESCE(NULLIF(status,''),'completed'), "
        "updated_at=COALESCE(updated_at, received_at), "
        "completed_at=COALESCE(completed_at, received_at), "
        "attempts=CASE WHEN attempts IS NULL OR attempts < 1 THEN 1 ELSE attempts END"
    )


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    _ensure_inbound_state_columns(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger_delivery_outbox(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            canonical_user_id INTEGER NOT NULL,
            event_key TEXT NOT NULL,
            action TEXT NOT NULL DEFAULT '',
            replies_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at TEXT NOT NULL,
            locked_at TEXT,
            lock_token TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            sent_at TEXT,
            UNIQUE(platform, event_key)
        )
        """.strip()
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messenger_delivery_outbox_due "
        "ON messenger_delivery_outbox(status, available_at, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messenger_delivery_outbox_user "
        "ON messenger_delivery_outbox(canonical_user_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messenger_webhook_events_status "
        "ON messenger_webhook_events(status, updated_at)"
    )
    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
