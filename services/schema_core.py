from __future__ import annotations

import logging
import sqlite3
from services.db import get_db, tx
from services.db.runtime import is_postgres_enabled


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _add_col(conn: sqlite3.Connection, table: str, col_ddl: str):
    """Idempotent ALTER TABLE ... ADD COLUMN.

    SQLite does not support `ADD COLUMN IF NOT EXISTS`, so we check manually.
    """
    col_ddl = (col_ddl or "").strip()
    if not col_ddl:
        return
    col_name = col_ddl.split()[0].strip('`"[]')
    try:
        if col_name in _cols(conn, table):
            return
    except sqlite3.Error:
        logging.getLogger(__name__).exception("Failed to inspect columns for %s.%s", table, col_name)
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_ddl}")


def init_db() -> None:
    """Create tables, apply one-time migrations, and ensure prod tables.

    SQLite remains the default engine, but Postgres now goes through the same
    schema/migration entrypoint via a compatibility adapter from services.db.core.
    """
    from services import schema_tables
    from services.migrations import apply_all_migrations

    with get_db() as conn:
        with tx(conn) as c:
            schema_tables.create_or_update_tables(c)
            # One-time migrations run inside the same transaction as schema adjustments.
            apply_all_migrations(c)

        # Prod tables are safe to ensure outside the transaction.
        ensure_prod_tables(conn)


def ensure_prod_tables(conn: sqlite3.Connection) -> None:
    """Tables used in prod for idempotency / queues / deliveries."""
    conn.execute(
        """
    CREATE TABLE IF NOT EXISTS idempotency (
        user_id INTEGER,
        key TEXT,
        created_at INTEGER,
        UNIQUE(user_id, key)
    )
    """.strip()
    )
    conn.execute(
        """
    CREATE TABLE IF NOT EXISTS pending_actions (
        user_id INTEGER,
        action TEXT,
        payload TEXT,
        created_at INTEGER
    )
    """.strip()
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deliveries(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            stage TEXT NOT NULL,
            scheduled_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            UNIQUE(user_id, kind, stage, scheduled_at)
        )
        """.strip()
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_deliveries_user_created_at
        ON deliveries(user_id, created_at)
        """.strip()
    )
