from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import migration_applied, mark_migration

NAME = 'user_audio_access_tokens_v1'
log = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        return
    log.info('Migration start: %s', NAME)
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS user_audio_access_tokens(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            sequence_key TEXT NOT NULL,
            anchor INTEGER NOT NULL,
            title TEXT,
            file_path TEXT NOT NULL,
            platform TEXT NOT NULL,
            created_at TEXT NOT NULL,
            first_accessed_at TEXT,
            last_accessed_at TEXT,
            access_count INTEGER NOT NULL DEFAULT 0
        )
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_user_audio_access_tokens_user ON user_audio_access_tokens(user_id, sequence_key, anchor)'
    )
    mark_migration(conn, NAME)
    log.info('Migration applied: %s', NAME)
