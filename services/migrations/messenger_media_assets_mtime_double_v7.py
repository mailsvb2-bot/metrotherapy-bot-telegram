from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import _is_postgres, mark_migration, migration_applied

NAME = "messenger_media_assets_mtime_double_v7"
log = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        return

    log.info("Migration start: %s", NAME)

    if _is_postgres():
        # PostgreSQL REAL is a 4-byte float and rounds large epoch timestamps,
        # which breaks media cache hits that compare file mtimes exactly.
        conn.execute(
            """
            ALTER TABLE messenger_media_assets
            ALTER COLUMN asset_mtime TYPE DOUBLE PRECISION
            USING asset_mtime::DOUBLE PRECISION
            """.strip()
        )

    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
