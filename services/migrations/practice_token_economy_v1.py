from __future__ import annotations

import logging
import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "practice_token_economy_v1"


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)

    conn.execute("CREATE TABLE IF NOT EXISTS practice_wallets(user_id INTEGER PRIMARY KEY, available_tokens INTEGER NOT NULL DEFAULT 0, reserved_tokens INTEGER NOT NULL DEFAULT 0, used_tokens INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS practice_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, event_type TEXT NOT NULL, amount INTEGER NOT NULL, balance_after INTEGER NOT NULL, reason TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', package_id TEXT, provider TEXT, provider_payment_id TEXT, idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_practice_ledger_user_id ON practice_ledger(user_id)")
    conn.execute("CREATE TABLE IF NOT EXISTS payment_token_grants(provider TEXT NOT NULL, provider_payment_id TEXT NOT NULL, user_id INTEGER NOT NULL, package_id TEXT NOT NULL, tokens_granted INTEGER NOT NULL, ledger_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(provider, provider_payment_id))")
    conn.execute("CREATE TABLE IF NOT EXISTS user_practice_preferences(user_id INTEGER PRIMARY KEY, delivery_mode TEXT NOT NULL DEFAULT 'single_daily', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS practice_reservations(reservation_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, status TEXT NOT NULL, session_id INTEGER, audio_anchor INTEGER, reason TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_practice_reservations_user_status ON practice_reservations(user_id, status)")

    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
