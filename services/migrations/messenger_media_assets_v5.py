from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import migration_applied, mark_migration

NAME = 'messenger_media_assets_v5'
log = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        return
    log.info('Migration start: %s', NAME)
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS messenger_media_assets(
            platform TEXT NOT NULL,
            asset_key TEXT NOT NULL,
            asset_path TEXT NOT NULL,
            asset_mtime REAL NOT NULL,
            asset_size INTEGER NOT NULL,
            remote_token TEXT NOT NULL,
            media_type TEXT NOT NULL DEFAULT 'audio',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_used_at TEXT NOT NULL,
            PRIMARY KEY(platform, asset_key)
        )
        '''.strip()
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_messenger_media_assets_used ON messenger_media_assets(platform, last_used_at)'
    )
    mark_migration(conn, NAME)
    log.info('Migration applied: %s', NAME)
