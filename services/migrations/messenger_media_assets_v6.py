from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import migration_applied, mark_migration

NAME = 'messenger_media_assets_v6'
log = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        return
    log.info('Migration start: %s', NAME)
    conn.execute('ALTER TABLE messenger_media_assets RENAME TO messenger_media_assets_old')
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
            PRIMARY KEY(platform, asset_key, media_type)
        )
        '''.strip()
    )
    conn.execute(
        '''
        INSERT INTO messenger_media_assets(
            platform, asset_key, asset_path, asset_mtime, asset_size,
            remote_token, media_type, created_at, updated_at, last_used_at
        )
        SELECT platform, asset_key, asset_path, asset_mtime, asset_size,
               remote_token, COALESCE(media_type, 'audio'), created_at, updated_at, last_used_at
        FROM messenger_media_assets_old
        '''.strip()
    )
    conn.execute('DROP TABLE messenger_media_assets_old')
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_messenger_media_assets_used ON messenger_media_assets(platform, last_used_at)'
    )
    mark_migration(conn, NAME)
    log.info('Migration applied: %s', NAME)
