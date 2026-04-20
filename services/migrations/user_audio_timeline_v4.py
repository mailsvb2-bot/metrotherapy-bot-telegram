from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import ensure_schema_migrations, migration_applied, mark_migration

log = logging.getLogger(__name__)
_MARK = 'user_audio_timeline_v4'


def apply(conn: sqlite3.Connection) -> None:
    ensure_schema_migrations(conn)
    if migration_applied(conn, _MARK):
        return
    log.info('Migration start: %s', _MARK)
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS user_audio_timeline(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            sequence_key TEXT NOT NULL,
            event_type TEXT NOT NULL,
            anchor INTEGER NULL,
            title TEXT NULL,
            platform TEXT NULL,
            token TEXT NULL,
            meta_json TEXT NULL,
            created_at TEXT NOT NULL
        )
        '''.strip()
    )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_user_audio_timeline_user_created ON user_audio_timeline(user_id, created_at DESC)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_user_audio_timeline_user_sequence ON user_audio_timeline(user_id, sequence_key, id DESC)')
    mark_migration(conn, _MARK)
    log.info('Migration applied: %s', _MARK)
