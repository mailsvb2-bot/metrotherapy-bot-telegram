from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "growth_conversion_bridge_state_v2"


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS growth_conversion_bridge_state(
            bridge_name TEXT PRIMARY KEY,
            last_event_id INTEGER NOT NULL DEFAULT 0,
            last_batch_size INTEGER NOT NULL DEFAULT 0,
            last_inserted INTEGER NOT NULL DEFAULT 0,
            last_duplicates INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """.strip()
    )
    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
