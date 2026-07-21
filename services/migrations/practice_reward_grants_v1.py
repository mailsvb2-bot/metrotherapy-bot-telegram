from __future__ import annotations

import logging
import sqlite3
from typing import Any

from services.migrations._helpers import mark_migration, migration_applied
from services.schema_core import _add_col, _cols

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


def _ensure_columns(conn: sqlite3.Connection) -> None:
    existing = _cols(conn, "bonus_grants")
    additions = {
        "reward_key": "reward_key TEXT",
        "tokens_granted": "tokens_granted INTEGER",
        "provider": "provider TEXT NOT NULL DEFAULT ''",
        "provider_payment_id": "provider_payment_id TEXT NOT NULL DEFAULT ''",
        "ledger_id": "ledger_id INTEGER",
    }
    for name, ddl in additions.items():
        if name not in existing:
            _add_col(conn, "bonus_grants", ddl)


def _backfill_legacy_bonus_grants(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT id, user_id, days, source, related_user_id, reward_key, ledger_id
        FROM bonus_grants
        WHERE COALESCE(days,0) > 0
        ORDER BY id ASC
        """.strip()
    ).fetchall()
    inserted_count = 0
    seen_referrals: set[int] = set()
    for row in rows:
        bonus_id = int(_value(row, "id", 0, 0) or 0)
        user_id = int(_value(row, "user_id", 1, 0) or 0)
        tokens = int(_value(row, "days", 2, 0) or 0)
        source = str(_value(row, "source", 3, "") or "").strip().lower()
        related_raw = _value(row, "related_user_id", 4)
        related_user_id = int(related_raw) if related_raw is not None else None
        existing_key = str(_value(row, "reward_key", 5, "") or "").strip()
        existing_ledger = int(_value(row, "ledger_id", 6, 0) or 0)
        if bonus_id <= 0 or user_id <= 0 or tokens <= 0:
            continue

        reward_type = "gift" if source == "gift" else "referral"
        if existing_key:
            reward_key = existing_key
        elif reward_type == "referral" and related_user_id is not None and related_user_id not in seen_referrals:
            reward_key = f"referral:{related_user_id}"
            seen_referrals.add(related_user_id)
        else:
            reward_key = f"legacy_bonus:{bonus_id}"
        ledger_key = f"reward:{reward_key}"

        conn.execute(
            """
            UPDATE bonus_grants
            SET source=?, reward_key=?, tokens_granted=COALESCE(tokens_granted, days),
                provider=CASE WHEN provider='' THEN 'legacy_bonus' ELSE provider END
            WHERE id=?
            """.strip(),
            (reward_type, reward_key, bonus_id),
        )
        if existing_ledger > 0:
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
            "UPDATE bonus_grants SET ledger_id=? WHERE id=?",
            (ledger_id, bonus_id),
        )
        inserted_count += 1
    return inserted_count


def apply(conn: sqlite3.Connection) -> None:
    log = logging.getLogger(__name__)
    if migration_applied(conn, NAME):
        log.info("Migration skipped (already applied): %s", NAME)
        return

    log.info("Migration start: %s", NAME)
    _ensure_columns(conn)
    backfilled = _backfill_legacy_bonus_grants(conn)
    # NULL reward keys remain allowed for legacy projections, while every
    # canonical reward gets a globally unique non-null key. A non-partial index
    # keeps ON CONFLICT(reward_key) portable across SQLite and PostgreSQL.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_bonus_grants_reward_key ON bonus_grants(reward_key)"
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bonus_grants_user_source
        ON bonus_grants(user_id, source, granted_at_utc)
        """.strip()
    )
    mark_migration(conn, NAME)
    log.info("Migration applied: %s backfilled=%s", NAME, backfilled)
