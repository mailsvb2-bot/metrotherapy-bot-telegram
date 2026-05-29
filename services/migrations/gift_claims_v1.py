from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "gift_claims_v1"


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gift_claims(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gift_token TEXT NOT NULL UNIQUE,
            buyer_user_id INTEGER NOT NULL DEFAULT 0,
            recipient_user_id INTEGER,
            package_id TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            provider_payment_id TEXT NOT NULL DEFAULT '',
            source_platform TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'created',
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP),
            paid_at TEXT,
            claimed_at TEXT
        )
        """.strip()
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_claims_status ON gift_claims(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_claims_recipient ON gift_claims(recipient_user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gift_claims_provider_payment ON gift_claims(provider, provider_payment_id)")
    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
