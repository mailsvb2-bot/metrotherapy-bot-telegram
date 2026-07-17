from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "payment_reconciliation_retry_v1"
log = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_reconciliation_retry(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            provider_payment_id TEXT NOT NULL,
            user_id INTEGER NOT NULL DEFAULT 0,
            event TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            available_at TEXT NOT NULL,
            locked_at TEXT,
            lock_token TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            UNIQUE(provider, provider_payment_id, event)
        )
        """.strip()
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payment_reconciliation_retry_due "
        "ON payment_reconciliation_retry(status, available_at, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payment_reconciliation_retry_payment "
        "ON payment_reconciliation_retry(provider, provider_payment_id, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_payment_reconciliation_retry_user "
        "ON payment_reconciliation_retry(user_id, id)"
    )
    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
