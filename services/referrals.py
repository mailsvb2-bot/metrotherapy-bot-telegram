from __future__ import annotations

from datetime import datetime
from core.time_utils import utc_now
from services.db import db
from config.settings import settings


def set_referral(referrer_id: int, referred_id: int) -> bool:
    if int(referrer_id) == int(referred_id):
        return False
    with db() as conn:
        row = conn.execute("SELECT referred_id FROM referrals WHERE referred_id=?", (int(referred_id),)).fetchone()
        if row:
            return False
        conn.execute(
            "INSERT INTO referrals(referred_id, referrer_id, joined_at, reward_given) VALUES(?,?,?,0)",
            (int(referred_id), int(referrer_id), utc_now().replace(microsecond=0).isoformat()),
        )
    return True


def get_referrer(referred_id: int) -> int | None:
    with db() as conn:
        row = conn.execute("SELECT referrer_id FROM referrals WHERE referred_id=?", (int(referred_id),)).fetchone()
    return int(row["referrer_id"]) if row else None


def reward_already_given(referred_id: int) -> bool:
    with db() as conn:
        row = conn.execute("SELECT reward_given FROM referrals WHERE referred_id=?", (int(referred_id),)).fetchone()
    return bool(row and int(row["reward_given"]) == 1)


def referrer_bonus_count(referrer_id: int) -> int:
    """Сколько бонусов уже начислено рефереру."""
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(1) AS n FROM referrals WHERE referrer_id=? AND reward_given=1",
            (int(referrer_id),),
        ).fetchone()
    return int(row["n"] if row and row["n"] is not None else 0)


def can_reward_referrer(referrer_id: int) -> bool:
    limit = int(getattr(settings, "REF_MAX_BONUSES", 10) or 10)
    if limit <= 0:
        return True
    return referrer_bonus_count(int(referrer_id)) < limit


def mark_reward_given(referred_id: int, reward_days: int):
    """Помечает, что бонус по рефералу начислен, и сохраняет событие в бонус-реестре.

    Важно по Установкам:
    - бонус ТОЛЬКО за оплативших;
    - доказуемость: отдельная запись в bonus_grants;
    - никаких секретов / внешних зависимостей.
    """
    with db() as conn:
        now = utc_now().replace(microsecond=0).isoformat()
        # fixed record in referrals
        conn.execute(
            "UPDATE referrals SET reward_given=1, reward_days=?, paid_at=COALESCE(paid_at, ?), bonus_applied=1, bonus_applied_at=? WHERE referred_id=?",
            (int(reward_days), now, now, int(referred_id)),
        )

        # create bonus grant for referrer (if known)
        row = conn.execute(
            "SELECT referrer_id FROM referrals WHERE referred_id=?",
            (int(referred_id),),
        ).fetchone()
        if row and (row[0] if not hasattr(row, 'keys') else row['referrer_id']):
            referrer_id = int(row[0] if not hasattr(row, 'keys') else row['referrer_id'])
            conn.execute(
                "INSERT INTO bonus_grants(user_id, days, source, related_user_id, granted_at_utc) VALUES(?,?,?,?,?)",
                (referrer_id, int(reward_days), 'referral', int(referred_id), now),
            )
