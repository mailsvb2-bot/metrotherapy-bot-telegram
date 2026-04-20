from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import migration_applied, mark_migration

NAME = 'user_messenger_runtime_v3'
log = logging.getLogger(__name__)


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        return
    log.info('Migration start: %s', NAME)
    conn.execute(
        '''
        CREATE UNIQUE INDEX IF NOT EXISTS idx_user_channel_identity_external_unique
        ON user_channel_identities(platform, external_user_id)
        WHERE external_user_id IS NOT NULL AND external_user_id != ''
        '''.strip()
    )
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS messenger_webhook_events(
            platform TEXT NOT NULL,
            event_key TEXT NOT NULL,
            received_at TEXT NOT NULL,
            payload_hash TEXT,
            PRIMARY KEY(platform, event_key)
        )
        '''.strip()
    )
    conn.execute(
        'CREATE INDEX IF NOT EXISTS idx_messenger_webhook_events_received ON messenger_webhook_events(received_at)'
    )
    mark_migration(conn, NAME)
    log.info('Migration applied: %s', NAME)
