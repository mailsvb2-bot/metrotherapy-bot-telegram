from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "messenger_delivery_reply_progress_v2"
log = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messenger_delivery_reply_progress(
            outbox_id BIGINT PRIMARY KEY,
            next_reply_index INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(outbox_id) REFERENCES messenger_delivery_outbox(id) ON DELETE CASCADE
        )
        """.strip()
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messenger_delivery_reply_progress_updated "
        "ON messenger_delivery_reply_progress(updated_at)"
    )
    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
