from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import migration_applied, mark_migration

NAME = 'user_delivery_preferences_v6'
log = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        return
    log.info('Migration start: %s', NAME)
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS user_delivery_preferences(
            user_id INTEGER PRIMARY KEY,
            timezone TEXT,
            quiet_hours_enabled INTEGER NOT NULL DEFAULT 0,
            quiet_start TEXT,
            quiet_end TEXT,
            morning_channel TEXT,
            evening_channel TEXT,
            updated_at TEXT NOT NULL
        )
        '''.strip()
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_user_delivery_preferences_updated_at ON user_delivery_preferences(updated_at)'
    )
    mark_migration(conn, NAME)
    log.info('Migration applied: %s', NAME)
