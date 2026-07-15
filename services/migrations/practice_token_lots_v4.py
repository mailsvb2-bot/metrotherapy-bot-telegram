from __future__ import annotations

import sqlite3

from services.migrations._helpers import mark_migration, migration_applied

NAME = "practice_token_lots_v4"


def apply(conn: sqlite3.Connection) -> None:
    if migration_applied(conn, NAME):
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS practice_token_lots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_key TEXT NOT NULL UNIQUE,
            user_id BIGINT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            provider_payment_id TEXT NOT NULL DEFAULT '',
            package_id TEXT NOT NULL DEFAULT '',
            granted_tokens INTEGER NOT NULL,
            available_tokens INTEGER NOT NULL,
            reserved_tokens INTEGER NOT NULL DEFAULT 0,
            used_tokens INTEGER NOT NULL DEFAULT 0,
            refund_held_tokens INTEGER NOT NULL DEFAULT 0,
            refunded_tokens INTEGER NOT NULL DEFAULT 0,
            refundable INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK(granted_tokens >= 0),
            CHECK(available_tokens >= 0),
            CHECK(reserved_tokens >= 0),
            CHECK(used_tokens >= 0),
            CHECK(refund_held_tokens >= 0),
            CHECK(refunded_tokens >= 0),
            CHECK(available_tokens + reserved_tokens + used_tokens + refund_held_tokens + refunded_tokens = granted_tokens)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_practice_token_lots_user_available ON practice_token_lots(user_id, available_tokens, id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_practice_token_lots_payment ON practice_token_lots(provider, provider_payment_id) WHERE provider_payment_id <> ''"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS practice_reservation_lots(
            reservation_id TEXT NOT NULL,
            lot_id BIGINT NOT NULL,
            amount INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(reservation_id, lot_id),
            FOREIGN KEY(lot_id) REFERENCES practice_token_lots(id),
            CHECK(amount > 0),
            CHECK(status IN ('reserved','consumed','released'))
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_practice_reservation_lots_lot ON practice_reservation_lots(lot_id, status)"
    )

    # Existing aggregate wallets have no trustworthy payment provenance. Keep
    # them usable in an explicit non-refundable legacy lot rather than guessing
    # which historical purchase supplied a token.
    conn.execute(
        """
        INSERT INTO practice_token_lots(
            lot_key, user_id, provider, provider_payment_id, package_id,
            granted_tokens, available_tokens, reserved_tokens, used_tokens,
            refundable
        )
        SELECT 'legacy:' || user_id, user_id, 'legacy', '', '',
               available_tokens + reserved_tokens + used_tokens,
               available_tokens, reserved_tokens, used_tokens, 0
        FROM practice_wallets
        WHERE available_tokens + reserved_tokens + used_tokens > 0
        ON CONFLICT DO NOTHING
        """
    )
    # Map any active historical reservations to the legacy lot. Each current
    # reservation is amount=1 in the canonical schema, but the query is generic.
    conn.execute(
        """
        INSERT INTO practice_reservation_lots(reservation_id, lot_id, amount, status)
        SELECT r.reservation_id, l.id, r.amount, 'reserved'
        FROM practice_reservations r
        JOIN practice_token_lots l ON l.user_id=r.user_id AND l.lot_key='legacy:' || r.user_id
        WHERE r.status='reserved'
        ON CONFLICT DO NOTHING
        """
    )
    mark_migration(conn, NAME)
