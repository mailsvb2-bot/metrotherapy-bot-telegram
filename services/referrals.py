from __future__ import annotations

from config.settings import settings
from core.time_utils import utc_now
from services.db import db


def _row_value(row, key: str, index: int, default=None):
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


def set_referral(referrer_id: int, referred_id: int) -> bool:
    referrer = int(referrer_id)
    referred = int(referred_id)
    if referrer <= 0 or referred <= 0 or referrer == referred:
        return False
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO referrals(referred_id, referrer_id, joined_at, reward_given)
            VALUES(?,?,?,0)
            ON CONFLICT(referred_id) DO NOTHING
            """.strip(),
            (referred, referrer, utc_now().replace(microsecond=0).isoformat()),
        )
        return int(getattr(cursor, "rowcount", 0) or 0) == 1


def get_referrer(referred_id: int) -> int | None:
    with db() as conn:
        row = conn.execute(
            "SELECT referrer_id FROM referrals WHERE referred_id=? LIMIT 1",
            (int(referred_id),),
        ).fetchone()
    raw = _row_value(row, "referrer_id", 0)
    return int(raw) if raw is not None else None


def reward_already_given(referred_id: int) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT reward_given FROM referrals WHERE referred_id=? LIMIT 1",
            (int(referred_id),),
        ).fetchone()
    return bool(row and int(_row_value(row, "reward_given", 0, 0) or 0) == 1)


def referrer_bonus_count(referrer_id: int) -> int:
    """How many paid-referral rewards have been recorded for this referrer."""
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(1) AS n FROM referrals WHERE referrer_id=? AND COALESCE(reward_given,0)=1",
            (int(referrer_id),),
        ).fetchone()
    return int(_row_value(row, "n", 0, 0) or 0)


def can_reward_referrer(referrer_id: int) -> bool:
    limit = int(getattr(settings, "REF_MAX_BONUSES", 10) or 10)
    if limit <= 0:
        return True
    return referrer_bonus_count(int(referrer_id)) < limit


def mark_reward_given(referred_id: int, reward_days: int) -> bool:
    """Compatibility entrypoint backed by the canonical practice-token ledger.

    Legacy subscription payment code still calls this function. The reward itself
    is now an idempotent practice-token grant, so hard token enforcement and the
    user-visible balance agree with the referral message.
    """
    from services.reward_tokens import grant_referral_reward

    result = grant_referral_reward(
        referred_user_id=int(referred_id),
        reward_tokens=int(reward_days),
        provider="legacy_subscription",
        provider_payment_id=f"legacy-referral:{int(referred_id)}",
        source="legacy_paid_referral",
    )
    return bool(result and result.inserted)
