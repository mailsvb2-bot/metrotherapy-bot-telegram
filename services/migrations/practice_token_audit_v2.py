from __future__ import annotations

import logging
import re
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "practice_token_audit_v2"
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _TABLE_RE.match(table):
        raise ValueError(f"Unsafe table name: {table!r}")
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if not _TABLE_RE.match(table):
        raise ValueError(f"Unsafe table name: {table!r}")
    if not _TABLE_RE.match(column):
        raise ValueError(f"Unsafe column name: {column!r}")
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)

    _add_column(conn, "practice_wallets", "refunded_tokens", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "practice_ledger", "reserved_after", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "practice_ledger", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
    _add_column(conn, "practice_ledger", "session_id", "INTEGER")
    _add_column(conn, "practice_ledger", "audio_anchor", "INTEGER")
    _add_column(conn, "practice_ledger", "reservation_id", "TEXT")
    _add_column(conn, "practice_reservations", "expires_at", "TEXT")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_practice_reservations_status_expiry ON practice_reservations(status, expires_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_practice_ledger_reservation ON practice_ledger(reservation_id)")

    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
