from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "growth_conversion_outbox_v1"


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS growth_conversion_outbox(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversion_type TEXT NOT NULL,
            source_platform TEXT NOT NULL DEFAULT '',
            source_event TEXT NOT NULL DEFAULT '',
            external_event_id TEXT NOT NULL DEFAULT '',
            user_id INTEGER NOT NULL DEFAULT 0,
            amount_minor INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'RUB',
            attribution_json TEXT NOT NULL DEFAULT '{}',
            payload_json TEXT NOT NULL DEFAULT '{}',
            target_provider TEXT NOT NULL DEFAULT 'none',
            mode TEXT NOT NULL DEFAULT 'dry_run',
            status TEXT NOT NULL DEFAULT 'planned',
            dispatch_allowed INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """.strip()
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_growth_conversion_outbox_status "
        "ON growth_conversion_outbox(mode, status, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_growth_conversion_outbox_user "
        "ON growth_conversion_outbox(user_id, conversion_type, id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_growth_conversion_outbox_external "
        "ON growth_conversion_outbox(source_platform, external_event_id)"
    )

    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
