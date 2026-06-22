from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import migration_applied, mark_migration

NAME = "events_decision_tracking_v1"

def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    try:
        if migration_applied(conn, NAME):
            log.info("Migration skipped (already applied): %s", NAME)
            return
    except sqlite3.Error:
        return

    log.info("Migration start: %s", NAME)

    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
    except sqlite3.Error:
        mark_migration(conn, NAME)
        return

    def add(col: str, ddl: str) -> None:
        if col in cols:
            return
        try:
            conn.execute(f"ALTER TABLE events ADD COLUMN {ddl}")
        except sqlite3.Error:
            pass

    add("decision_id", "decision_id TEXT")
    add("correlation_id", "correlation_id TEXT")
    add("source", "source TEXT")
    add("event_type", "event_type TEXT")
    add("payload", "payload TEXT")
    add("timestamp_utc", "timestamp_utc TEXT")

    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
