from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "premium_entitlements_v1"


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS premium_entitlements(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            package_id TEXT NOT NULL,
            entitlement_type TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'manual',
            provider_payment_id TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """.strip()
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_premium_entitlements_user ON premium_entitlements(user_id, entitlement_type, status)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS premium_delivery_outbox(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            external_user_id TEXT,
            delivery_kind TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """.strip()
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_premium_delivery_outbox_status ON premium_delivery_outbox(status, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_premium_delivery_outbox_user ON premium_delivery_outbox(user_id, platform)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS consultation_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            external_user_id TEXT,
            package_id TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'manual',
            provider_payment_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            contact_payload TEXT NOT NULL DEFAULT '',
            admin_note TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """.strip()
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_consultation_requests_status ON consultation_requests(status, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_consultation_requests_user ON consultation_requests(user_id)")

    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
