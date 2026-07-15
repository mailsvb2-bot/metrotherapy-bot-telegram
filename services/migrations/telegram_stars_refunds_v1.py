from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "telegram_stars_refunds_v1"


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS telegram_stars_refunds(
            telegram_charge_id TEXT PRIMARY KEY,
            payment_user_id INTEGER NOT NULL,
            beneficiary_user_id INTEGER,
            package_id TEXT NOT NULL DEFAULT '',
            gift_token TEXT NOT NULL DEFAULT '',
            tokens_held INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new',
            attempts INTEGER NOT NULL DEFAULT 0,
            requested_by INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            provider_refunded_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """.strip()
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stars_refunds_status ON telegram_stars_refunds(status)")
    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
