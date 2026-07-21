from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "practice_reward_grants_v1"


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS practice_reward_grants(
            reward_key TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            reward_type TEXT NOT NULL,
            tokens_granted INTEGER NOT NULL,
            related_user_id INTEGER,
            provider TEXT NOT NULL DEFAULT '',
            provider_payment_id TEXT NOT NULL DEFAULT '',
            ledger_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """.strip()
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_practice_reward_user_type "
        "ON practice_reward_grants(user_id, reward_type, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_practice_reward_related "
        "ON practice_reward_grants(reward_type, related_user_id)"
    )
    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
