from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import ensure_schema_migrations, migration_applied, mark_migration


_NAME = 'user_channel_routing_v1'
log = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    ensure_schema_migrations(conn)
    if migration_applied(conn, _NAME):
        return

    log.info('Migration start: %s', _NAME)
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS user_channel_preferences(
            user_id INTEGER PRIMARY KEY,
            preferred_platform TEXT NOT NULL DEFAULT 'telegram',
            last_seen_platform TEXT NOT NULL DEFAULT 'telegram',
            updated_at TEXT NOT NULL
        )
        '''.strip()
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS user_channel_identities(
            user_id INTEGER NOT NULL,
            platform TEXT NOT NULL,
            external_user_id TEXT,
            username TEXT,
            display_name TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY(user_id, platform)
        )
        '''.strip()
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_user_channel_last_seen ON user_channel_identities(platform, last_seen_at)'
    )
    mark_migration(conn, _NAME)
    log.info('Migration applied: %s', _NAME)
