from __future__ import annotations

import logging
import sqlite3
from typing import Any

from services.migrations._helpers import mark_migration, migration_applied

NAME = "practice_reward_grants_v1"


def _value(row: Any, key: str, index: int, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, TypeError, IndexError):
            pass
    try:
        return row[index]
    except (TypeError, KeyError, IndexError):
        return default


def _backfill_legacy_bonus_grants(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT id, user_id, days, source, related_user_id
        FROM bonus_grants
        WHERE COALESCE(days,0) > 0
        ORDER BY id ASC
        """.strip()
    ).fetchall()
    inserted_count = 0
    for row in rows:
        bonus_id = int(_value(row, "id", 0, 0) or 0)
        user_id = int(_value(row, "user_id", 1, 0) or 0)
        tokens = int(_value(row, "days", 2, 0) or 0)
        source = str(_value(row, "source", 3, "") or "").strip().lower()
        related_raw = _value(row, "related_user_id", 4)
        related_user_id = int(related_raw) if related_raw is not None else None
        if bonus_id <= 0 or user_id <= 0 or tokens <= 0:
            continue

        reward_type = "gift" if source == "gift" else "referral"
        if reward_type == "referral" and related_user_id is not None:
            reward_key = f"referral:{related_user_id}"
        else:
            reward_key = f"legacy_bonus:{bonus_id}"
        ledger_key = f"reward:{reward_key}"

        claimed = conn.execute(
            """
            INSERT INTO practice_reward_grants(
                reward_key, user_id, reward_type, tokens_granted, related_user_id,
                provider, provider_payment_id, ledger_id
            ) VALUES(?,?,?,?,?,'legacy_bonus','',NULL)
            ON CONFLICT(reward_key) DO NOTHING
            """.strip(),
            (reward_key, user_id, reward_type, tokens, related_user_id),
        )
        if int(getattr(claimed, "rowcount", 0) or 0) <= 0:
            continue

        conn.execute(
            """
            INSERT INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens)
            VALUES(?,0,0,0)
            ON CONFLICT(user_id) DO NOTHING
            """.strip(),
            (user_id,),
        )
        conn.execute(
            """
            UPDATE practice_wallets
            SET available_tokens=COALESCE(available_tokens,0)+?, updated_at=CURRENT_TIMESTAMP
            WHERE user_id=?
            """.strip(),
            (tokens, user_id),
        )
        wallet = conn.execute(
            "SELECT available_tokens FROM practice_wallets WHERE user_id=?",
            (user_id,),
        ).fetchone()
        balance_after = int(_value(wallet, "available_tokens", 0, 0) or 0)
        conn.execute(
            """
            INSERT INTO practice_ledger(
                user_id, event_type, amount, balance_after, reason, source,
                package_id, provider, provider_payment_id, idempotency_key
            ) VALUES(?, 'grant', ?, ?, ?, ?, ?, 'legacy_bonus', '', ?)
            """.strip(),
            (
                user_id,
                tokens,
                balance_after,
                f"{reward_type}_reward_backfill",
                source or reward_type,
                f"reward:{reward_type}",
                ledger_key,
            ),
        )
        ledger = conn.execute(
            "SELECT id FROM practice_ledger WHERE idempotency_key=? LIMIT 1",
            (ledger_key,),
        ).fetchone()
        ledger_id = int(_value(ledger, "id", 0, 0) or 0)
        if ledger_id <= 0:
            raise RuntimeError("practice_reward_backfill_ledger_missing")
        conn.execute(
            """
            INSERT INTO practice_token_lots(
                lot_key, user_id, provider, provider_payment_id, package_id,
                granted_tokens, available_tokens, refundable
            ) VALUES(?,?,'legacy_bonus','',?,?,?,0)
            ON CONFLICT(lot_key) DO NOTHING
            """.strip(),
            (ledger_key, user_id, f"reward:{reward_type}", tokens, tokens),
        )
        conn.execute(
            "UPDATE practice_reward_grants SET ledger_id=? WHERE reward_key=?",
            (ledger_id, reward_key),
        )
        inserted_count += 1
    return inserted_count


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS practice_reward_grants(
            reward_key TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            reward_type TEXT NOT NULL,
            tokens_granted INTEGER NOT NULL,
            related_user_id INTEGER,
            provider TEXT NOT NULL DEFAULT '',
            provider_payment_id TEXT NOT NULL DEFAULT '',
            ledger_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """.strip()
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_practice_reward_user_type "
        "ON practice_reward_grants(user_id, reward_type, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_practice_reward_related "
        "ON practice_reward_grants(reward_type, related_user_id)"
    )
    backfilled = _backfill_legacy_bonus_grants(conn)
    mark_migration(conn, NAME)
    log.info("Migration applied: %s backfilled=%s", NAME, backfilled)
