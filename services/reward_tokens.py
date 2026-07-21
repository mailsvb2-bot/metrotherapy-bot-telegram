from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from config.settings import settings
from core.time_utils import utc_now
from services.db import db, tx
from services.practice_token_lots import create_lot_in_conn
from services.practice_tokens_wallet import (
    canonical_practice_user_id,
    ensure_wallet,
    get_wallet_in_conn,
    insert_ledger,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RewardGrantResult:
    inserted: bool
    user_id: int
    tokens: int
    wallet_balance: int
    reward_type: str
    reward_key: str
    reason: str = ""


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
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


def _reward_key(value: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise ValueError("practice_reward_key_required")
    if len(key) > 220:
        raise ValueError("practice_reward_key_too_long")
    return key


def _lock_wallet(conn: Any, user_id: int) -> None:
    ensure_wallet(conn, int(user_id))
    cursor = conn.execute(
        "UPDATE practice_wallets SET updated_at=updated_at WHERE user_id=?",
        (int(user_id),),
    )
    if int(getattr(cursor, "rowcount", 0) or 0) != 1:
        raise RuntimeError("practice_reward_wallet_lock_failed")


def _existing_reward(conn: Any, reward_key: str) -> Any:
    return conn.execute(
        """
        SELECT reward_key, user_id, source AS reward_type,
               COALESCE(tokens_granted, days) AS tokens_granted,
               related_user_id, provider, provider_payment_id, ledger_id
        FROM bonus_grants
        WHERE reward_key=?
        LIMIT 1
        """.strip(),
        (reward_key,),
    ).fetchone()


def _duplicate_result(
    conn: Any,
    row: Any,
    *,
    expected_user_id: int,
    expected_reward_type: str,
    expected_related_user_id: int | None,
    reward_key: str,
) -> RewardGrantResult:
    stored_user_id = int(_row_value(row, "user_id", 1, 0) or 0)
    stored_type = str(_row_value(row, "reward_type", 2, "") or "")
    stored_tokens = int(_row_value(row, "tokens_granted", 3, 0) or 0)
    stored_related_raw = _row_value(row, "related_user_id", 4)
    stored_related = int(stored_related_raw) if stored_related_raw is not None else None
    if (
        stored_user_id != int(expected_user_id)
        or stored_type != str(expected_reward_type)
        or stored_related != expected_related_user_id
    ):
        raise RuntimeError("practice_reward_idempotency_conflict")
    wallet = get_wallet_in_conn(conn, int(expected_user_id))
    return RewardGrantResult(
        inserted=False,
        user_id=int(expected_user_id),
        tokens=stored_tokens,
        wallet_balance=int(wallet.available_tokens),
        reward_type=str(expected_reward_type),
        reward_key=reward_key,
        reason="already_granted",
    )


def _grant_reward_in_conn(
    conn: Any,
    *,
    user_id: int,
    tokens: int,
    reward_type: str,
    reward_key: str,
    related_user_id: int | None,
    provider: str,
    provider_payment_id: str,
    source: str,
) -> RewardGrantResult:
    uid = int(user_id)
    amount = int(tokens)
    if uid <= 0:
        raise ValueError("practice_reward_user_id_required")
    if amount <= 0:
        raise ValueError("practice_reward_amount_must_be_positive")
    kind = str(reward_type or "").strip().lower()
    if kind not in {"referral", "gift"}:
        raise ValueError("practice_reward_type_invalid")
    key = _reward_key(reward_key)

    _lock_wallet(conn, uid)
    existing = _existing_reward(conn, key)
    if existing is not None:
        return _duplicate_result(
            conn,
            existing,
            expected_user_id=uid,
            expected_reward_type=kind,
            expected_related_user_id=related_user_id,
            reward_key=key,
        )

    claimed = conn.execute(
        """
        INSERT INTO bonus_grants(
            user_id, days, source, related_user_id, granted_at_utc,
            reward_key, tokens_granted, provider, provider_payment_id, ledger_id
        ) VALUES(?,?,?,?,?,?,?,?,?,NULL)
        ON CONFLICT(reward_key) DO NOTHING
        """.strip(),
        (
            uid,
            amount,
            kind,
            int(related_user_id) if related_user_id is not None else None,
            utc_now().replace(microsecond=0).isoformat(),
            key,
            amount,
            str(provider or ""),
            str(provider_payment_id or ""),
        ),
    )
    if int(getattr(claimed, "rowcount", 0) or 0) <= 0:
        concurrent = _existing_reward(conn, key)
        if concurrent is None:
            raise RuntimeError("practice_reward_claim_failed")
        return _duplicate_result(
            conn,
            concurrent,
            expected_user_id=uid,
            expected_reward_type=kind,
            expected_related_user_id=related_user_id,
            reward_key=key,
        )

    conn.execute(
        """
        UPDATE practice_wallets
        SET available_tokens=COALESCE(available_tokens,0)+?, updated_at=CURRENT_TIMESTAMP
        WHERE user_id=?
        """.strip(),
        (amount, uid),
    )
    wallet = get_wallet_in_conn(conn, uid)
    ledger_key = f"reward:{key}"
    ledger_id = insert_ledger(
        conn,
        user_id=uid,
        event_type="grant",
        amount=amount,
        balance_after=int(wallet.available_tokens),
        reason=f"{kind}_reward",
        source=str(source or kind),
        package_id=f"reward:{kind}",
        provider=str(provider or "reward"),
        provider_payment_id=str(provider_payment_id or ""),
        idempotency_key=ledger_key,
    )
    # The paid package already owns the canonical (provider, payment_id) lot.
    # Reward lots use a separate provider namespace while the original payment
    # provenance remains in bonus_grants and practice_ledger.
    create_lot_in_conn(
        conn,
        lot_key=ledger_key,
        user_id=uid,
        provider=f"reward_{kind}",
        provider_payment_id=key,
        package_id=f"reward:{kind}",
        amount=amount,
        refundable=False,
    )
    updated = conn.execute(
        "UPDATE bonus_grants SET ledger_id=? WHERE reward_key=? AND ledger_id IS NULL",
        (ledger_id, key),
    )
    if int(getattr(updated, "rowcount", 0) or 0) != 1:
        raise RuntimeError("practice_reward_finalize_failed")
    return RewardGrantResult(
        inserted=True,
        user_id=uid,
        tokens=amount,
        wallet_balance=int(wallet.available_tokens),
        reward_type=kind,
        reward_key=key,
        reason="granted",
    )


def _configured_referral_tokens(package_tokens: int) -> int:
    if int(package_tokens) >= 30:
        return int(getattr(settings, "REF_BONUS_MONTH_DAYS", 30) or 30)
    return int(getattr(settings, "REF_BONUS_WEEK_DAYS", 7) or 7)


def grant_referral_reward(
    *,
    referred_user_id: int,
    reward_tokens: int,
    provider: str,
    provider_payment_id: str,
    source: str = "referral",
) -> RewardGrantResult | None:
    referred_id = int(referred_user_id)
    if referred_id <= 0:
        raise ValueError("referred_user_id_required")

    with db() as conn:
        row = conn.execute(
            "SELECT referrer_id FROM referrals WHERE referred_id=? LIMIT 1",
            (referred_id,),
        ).fetchone()
    if row is None:
        return None
    raw_referrer = int(_row_value(row, "referrer_id", 0, 0) or 0)
    if raw_referrer <= 0 or raw_referrer == referred_id:
        return None
    referrer_id = canonical_practice_user_id(raw_referrer)
    reward_key = f"referral:{referred_id}"

    with db() as conn:
        with tx(conn):
            _lock_wallet(conn, referrer_id)
            referral = conn.execute(
                "SELECT referrer_id FROM referrals WHERE referred_id=? LIMIT 1",
                (referred_id,),
            ).fetchone()
            if referral is None or int(_row_value(referral, "referrer_id", 0, 0) or 0) != raw_referrer:
                return None

            existing = _existing_reward(conn, reward_key)
            if existing is not None:
                return _duplicate_result(
                    conn,
                    existing,
                    expected_user_id=referrer_id,
                    expected_reward_type="referral",
                    expected_related_user_id=referred_id,
                    reward_key=reward_key,
                )

            limit = int(getattr(settings, "REF_MAX_BONUSES", 10) or 10)
            if limit > 0:
                count_row = conn.execute(
                    """
                    SELECT COUNT(1) AS n
                    FROM referrals
                    WHERE referrer_id=? AND COALESCE(reward_given,0)=1 AND referred_id<>?
                    """.strip(),
                    (raw_referrer, referred_id),
                ).fetchone()
                rewarded = int(_row_value(count_row, "n", 0, 0) or 0)
                if rewarded >= limit:
                    wallet = get_wallet_in_conn(conn, referrer_id)
                    return RewardGrantResult(
                        inserted=False,
                        user_id=referrer_id,
                        tokens=0,
                        wallet_balance=int(wallet.available_tokens),
                        reward_type="referral",
                        reward_key=reward_key,
                        reason="limit_reached",
                    )

            result = _grant_reward_in_conn(
                conn,
                user_id=referrer_id,
                tokens=int(reward_tokens),
                reward_type="referral",
                reward_key=reward_key,
                related_user_id=referred_id,
                provider=provider,
                provider_payment_id=provider_payment_id,
                source=source,
            )
            if result.inserted:
                now = utc_now().replace(microsecond=0).isoformat()
                conn.execute(
                    """
                    UPDATE referrals
                    SET reward_given=1, reward_days=?, paid_at=COALESCE(paid_at, ?),
                        bonus_applied=1, bonus_applied_at=COALESCE(bonus_applied_at, ?)
                    WHERE referred_id=? AND referrer_id=?
                    """.strip(),
                    (int(result.tokens), now, now, referred_id, raw_referrer),
                )
            return result


def grant_referral_reward_for_payment(
    *,
    referred_user_id: int,
    package_tokens: int,
    provider: str,
    provider_payment_id: str,
) -> RewardGrantResult | None:
    return grant_referral_reward(
        referred_user_id=int(referred_user_id),
        reward_tokens=_configured_referral_tokens(int(package_tokens)),
        provider=str(provider or "payment"),
        provider_payment_id=str(provider_payment_id or ""),
        source="paid_referral",
    )


def grant_gift_buyer_reward(
    *,
    buyer_user_id: int,
    package_tokens: int,
    provider: str,
    provider_payment_id: str,
    gift_token: str,
) -> RewardGrantResult:
    buyer_id = canonical_practice_user_id(int(buyer_user_id))
    amount = 5 if int(package_tokens) >= 20 else 3
    payment_key = str(provider_payment_id or "").strip() or str(gift_token or "").strip()
    if not payment_key:
        raise ValueError("gift_reward_payment_identity_required")
    key = f"gift_buyer:{str(provider or 'gift')}:{payment_key}"
    with db() as conn:
        with tx(conn):
            return _grant_reward_in_conn(
                conn,
                user_id=buyer_id,
                tokens=amount,
                reward_type="gift",
                reward_key=key,
                related_user_id=None,
                provider=str(provider or "gift"),
                provider_payment_id=str(provider_payment_id or gift_token or ""),
                source="paid_gift",
            )
