from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.time_utils import utc_now
from services.db import db, tx
from services.practice_tokens_wallet import get_wallet_in_conn, insert_ledger


@dataclass(frozen=True)
class RewardRevocationResult:
    found: bool
    revoked: bool
    tokens: int
    debt_tokens: int
    reason: str


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


def revoke_reward_in_conn(
    conn: Any,
    *,
    reward_key: str,
    reason: str,
    source: str,
) -> RewardRevocationResult:
    key = str(reward_key or "").strip()
    if not key:
        raise ValueError("reward_revocation_key_required")
    reward = conn.execute(
        """
        SELECT user_id, COALESCE(tokens_granted, days) AS tokens,
               source, reward_status, provider, provider_payment_id
        FROM bonus_grants
        WHERE reward_key=?
        LIMIT 1
        """.strip(),
        (key,),
    ).fetchone()
    if reward is None:
        return RewardRevocationResult(False, False, 0, 0, "reward_not_found")

    user_id = int(_value(reward, "user_id", 0, 0) or 0)
    tokens = int(_value(reward, "tokens", 1, 0) or 0)
    reward_type = str(_value(reward, "source", 2, "reward") or "reward")
    status = str(_value(reward, "reward_status", 3, "active") or "active")
    provider = str(_value(reward, "provider", 4, "reward") or "reward")
    provider_payment_id = str(_value(reward, "provider_payment_id", 5, "") or "")
    if status == "revoked":
        return RewardRevocationResult(True, False, tokens, 0, "already_revoked")
    if user_id <= 0 or tokens <= 0:
        return RewardRevocationResult(True, False, tokens, max(tokens, 0), "reward_provenance_invalid")

    lot_key = f"reward:{key}"
    lot = conn.execute(
        """
        SELECT id, available_tokens, reserved_tokens, used_tokens,
               refund_held_tokens, refunded_tokens
        FROM practice_token_lots
        WHERE lot_key=? AND user_id=?
        LIMIT 1
        """.strip(),
        (lot_key, user_id),
    ).fetchone()
    if lot is None:
        return RewardRevocationResult(True, False, tokens, tokens, "reward_lot_missing")

    available = int(_value(lot, "available_tokens", 1, 0) or 0)
    reserved = int(_value(lot, "reserved_tokens", 2, 0) or 0)
    used = int(_value(lot, "used_tokens", 3, 0) or 0)
    held = int(_value(lot, "refund_held_tokens", 4, 0) or 0)
    already_refunded = int(_value(lot, "refunded_tokens", 5, 0) or 0)
    debt = max(0, reserved + used + held + max(0, tokens - available - reserved - used - held - already_refunded))
    if available != tokens or reserved or used or held or already_refunded:
        return RewardRevocationResult(True, False, tokens, debt, "reward_already_used_or_reserved")

    lock = conn.execute(
        "UPDATE practice_wallets SET updated_at=updated_at WHERE user_id=?",
        (user_id,),
    )
    if int(getattr(lock, "rowcount", 0) or 0) != 1:
        raise RuntimeError("reward_revocation_wallet_lock_failed")
    wallet = get_wallet_in_conn(conn, user_id)
    if int(wallet.available_tokens) < tokens:
        return RewardRevocationResult(
            True,
            False,
            tokens,
            tokens - int(wallet.available_tokens),
            "reward_wallet_balance_conflict",
        )

    wallet_update = conn.execute(
        """
        UPDATE practice_wallets
        SET available_tokens=available_tokens-?, refunded_tokens=refunded_tokens+?,
            updated_at=CURRENT_TIMESTAMP
        WHERE user_id=? AND available_tokens>=?
        """.strip(),
        (tokens, tokens, user_id, tokens),
    )
    if int(getattr(wallet_update, "rowcount", 0) or 0) != 1:
        raise RuntimeError("reward_revocation_wallet_race")

    lot_update = conn.execute(
        """
        UPDATE practice_token_lots
        SET available_tokens=0, refunded_tokens=refunded_tokens+?, refundable=0,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND available_tokens=? AND reserved_tokens=0 AND used_tokens=0
          AND refund_held_tokens=0 AND refunded_tokens=0
        """.strip(),
        (tokens, int(_value(lot, "id", 0, 0) or 0), tokens),
    )
    if int(getattr(lot_update, "rowcount", 0) or 0) != 1:
        raise RuntimeError("reward_revocation_lot_race")

    wallet_after = get_wallet_in_conn(conn, user_id)
    insert_ledger(
        conn,
        user_id=user_id,
        event_type="revoke",
        amount=-tokens,
        balance_after=int(wallet_after.available_tokens),
        reason=str(reason or "reward_revoked"),
        source=str(source or "reward_revocation"),
        package_id=f"reward:{reward_type}",
        provider=provider,
        provider_payment_id=provider_payment_id,
        idempotency_key=f"reward_revoke:{key}",
    )
    reward_update = conn.execute(
        """
        UPDATE bonus_grants
        SET reward_status='revoked', revoked_at=COALESCE(revoked_at, ?)
        WHERE reward_key=? AND COALESCE(reward_status,'active')='active'
        """.strip(),
        (utc_now().replace(microsecond=0).isoformat(), key),
    )
    if int(getattr(reward_update, "rowcount", 0) or 0) != 1:
        raise RuntimeError("reward_revocation_finalize_failed")
    return RewardRevocationResult(True, True, tokens, 0, "revoked")


def revoke_reward(*, reward_key: str, reason: str, source: str) -> RewardRevocationResult:
    with db() as conn:
        with tx(conn):
            return revoke_reward_in_conn(
                conn,
                reward_key=reward_key,
                reason=reason,
                source=source,
            )
