from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from core.time_utils import utc_now
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


def _remaining_tokens(tokens: int, granted_at: Any) -> int:
    """Translate the legacy calendar-day bonus into its remaining entitlement."""
    try:
        granted = datetime.fromisoformat(str(granted_at))
        if granted.tzinfo is None:
            granted = granted.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        # Corrupted accounting history must not become a fresh balance.
        return 0
    elapsed_days = max(
        0,
        (utc_now().astimezone(timezone.utc).date() - granted.astimezone(timezone.utc).date()).days,
    )
    return max(0, int(tokens) - elapsed_days)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    existing = _cols(conn, "bonus_grants")
    additions = {
        "reward_key": "reward_key TEXT",
        "tokens_granted": "tokens_granted INTEGER",
        "provider": "provider TEXT NOT NULL DEFAULT ''",
        "provider_payment_id": "provider_payment_id TEXT NOT NULL DEFAULT ''",
        "ledger_id": "ledger_id INTEGER",
        "reward_status": "reward_status TEXT NOT NULL DEFAULT 'active'",
        "revoked_at": "revoked_at TEXT",
    }
    for name, ddl in additions.items():
        if name not in existing:
            _add_col(conn, "bonus_grants", ddl)


def _backfill_legacy_bonus_grants(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT id, user_id, days, source, related_user_id, granted_at_utc,
               reward_key, ledger_id
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
        original_tokens = int(_value(row, "days", 2, 0) or 0)
        source = str(_value(row, "source", 3, "") or "").strip().lower()
        related_raw = _value(row, "related_user_id", 4)
        related_user_id = int(related_raw) if related_raw is not None else None
        granted_at = _value(row, "granted_at_utc", 5)
        existing_key = str(_value(row, "reward_key", 6, "") or "").strip()
        existing_ledger = int(_value(row, "ledger_id", 7, 0) or 0)
        if bonus_id <= 0 or user_id <= 0 or original_tokens <= 0:
            continue

        reward_type = "gift" if source == "gift" else "referral"
        if existing_key:
            reward_key = existing_key
        elif (
            reward_type == "referral"
            and related_user_id is not None
            and related_user_id not in seen_referrals
        ):
            reward_key = f"referral:{related_user_id}"
            seen_referrals.add(related_user_id)
        else:
            reward_key = f"legacy_bonus:{bonus_id}"
        ledger_key = f"reward:{reward_key}"
        remaining_tokens = _remaining_tokens(original_tokens, granted_at)
        status = "active" if remaining_tokens > 0 else "expired"

        conn.execute(
            """
            UPDATE bonus_grants
            SET source=?, reward_key=?, tokens_granted=COALESCE(tokens_granted, days),
                provider=CASE WHEN provider='' THEN 'legacy_bonus' ELSE provider END,
                reward_status=CASE
                    WHEN ledger_id IS NOT NULL THEN COALESCE(NULLIF(reward_status,''),'active')
                    ELSE ?
                END
            WHERE id=?
            """.strip(),
            (reward_type, reward_key, status, bonus_id),
        )
        if existing_ledger > 0 or remaining_tokens <= 0:
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
            (remaining_tokens, user_id),
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
                remaining_tokens,
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
            (
                ledger_key,
                user_id,
                f"reward:{reward_type}",
                remaining_tokens,
                remaining_tokens,
            ),
        )
        conn.execute("UPDATE bonus_grants SET ledger_id=? WHERE id=?", (ledger_id, bonus_id))
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
