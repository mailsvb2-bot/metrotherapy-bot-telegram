from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "admin_ad_links_v1"
logger = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    logger.info("Migration start: %s", NAME)
    if migration_applied(conn, NAME):
        logger.info("Migration already applied: %s", NAME)
        return

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_ad_links(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            campaign TEXT NOT NULL,
            creative TEXT NOT NULL,
            ad_spend TEXT NOT NULL DEFAULT '',
            start_payload TEXT NOT NULL,
            url TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """.strip()
    )
    mark_migration(conn, NAME)
    logger.info("Migration applied: %s", NAME)
