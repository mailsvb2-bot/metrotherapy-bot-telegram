from __future__ import annotations

import sqlite3


def ensure_schema_migrations(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations(
            name TEXT PRIMARY KEY,
            applied_at_utc TEXT NOT NULL
        )
        """.strip()
    )


def migration_applied(conn: sqlite3.Connection, name: str) -> bool:
    ensure_schema_migrations(conn)
    row = conn.execute("SELECT 1 FROM schema_migrations WHERE name=?", (name,)).fetchone()
    return bool(row)


def mark_migration(conn: sqlite3.Connection, name: str) -> None:
    ensure_schema_migrations(conn)
    conn.execute(
        "INSERT OR REPLACE INTO schema_migrations(name, applied_at_utc) VALUES(?, datetime('now'))",
        (name,),
    )



def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)
