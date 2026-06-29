from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "gift_claims_recipient_hint_v2"


def _cols(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(gift_claims)").fetchall()
    return {str(row["name"] if hasattr(row, "keys") else row[1]) for row in rows}


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    if "recipient_hint" not in _cols(conn):
        conn.execute("ALTER TABLE gift_claims ADD COLUMN recipient_hint TEXT NOT NULL DEFAULT ''")
    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
