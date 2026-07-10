from __future__ import annotations

import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

MIGRATION_NAME = "growth_apply_review_confirmations_v4"


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, MIGRATION_NAME):
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS growth_apply_confirmations (
            token_hash TEXT PRIMARY KEY,
            request_id INTEGER NOT NULL,
            decision TEXT NOT NULL,
            admin_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            CHECK (decision IN ('approve','reject')),
            CHECK (status IN ('pending','consumed','cancelled','expired')),
            FOREIGN KEY(request_id) REFERENCES growth_apply_requests(id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_growth_apply_confirmations_admin ON growth_apply_confirmations(admin_id, status, expires_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_growth_apply_confirmations_request ON growth_apply_confirmations(request_id, status)"
    )
    mark_migration(conn, MIGRATION_NAME)
