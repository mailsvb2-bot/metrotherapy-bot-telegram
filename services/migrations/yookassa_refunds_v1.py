from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "yookassa_refunds_v1"
log = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS yookassa_refunds(
            refund_id TEXT PRIMARY KEY,
            payment_id TEXT NOT NULL,
            user_id INTEGER NOT NULL DEFAULT 0,
            package_id TEXT NOT NULL DEFAULT '',
            gift_token TEXT NOT NULL DEFAULT '',
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            payment_amount_minor INTEGER NOT NULL DEFAULT 0,
            cumulative_refunded_minor INTEGER NOT NULL DEFAULT 0,
            tokens_affected INTEGER NOT NULL DEFAULT 0,
            debt_tokens INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'received',
            problem TEXT NOT NULL DEFAULT '',
            provider_raw TEXT NOT NULL DEFAULT '',
            processed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """.strip()
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_yookassa_refunds_payment "
        "ON yookassa_refunds(payment_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_yookassa_refunds_status "
        "ON yookassa_refunds(status, updated_at)"
    )
    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
