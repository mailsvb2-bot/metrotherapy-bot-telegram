from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import migration_applied, mark_migration

NAME = 'user_audio_progress_state_v2'
log = logging.getLogger(__name__)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {row[1] for row in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    if column not in cols:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {ddl}')


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        return
    log.info('Migration start: %s', NAME)
    _ensure_column(conn, 'user_audio_progress', 'last_confirmed_at', 'last_confirmed_at TEXT')
    _ensure_column(conn, 'user_audio_progress', 'pending_anchor', 'pending_anchor INTEGER')
    _ensure_column(conn, 'user_audio_progress', 'pending_title', 'pending_title TEXT')
    _ensure_column(conn, 'user_audio_progress', 'pending_path', 'pending_path TEXT')
    _ensure_column(conn, 'user_audio_progress', 'pending_platform', 'pending_platform TEXT')
    _ensure_column(conn, 'user_audio_progress', 'pending_token', 'pending_token TEXT')
    _ensure_column(conn, 'user_audio_progress', 'pending_delivered_at', 'pending_delivered_at TEXT')
    conn.execute(
        '''
        UPDATE user_audio_progress
        SET last_confirmed_at = COALESCE(last_confirmed_at, delivered_at)
        WHERE last_anchor IS NOT NULL
        '''
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_user_audio_progress_pending ON user_audio_progress(pending_platform, pending_delivered_at)'
    )
    mark_migration(conn, NAME)
    log.info('Migration applied: %s', NAME)
