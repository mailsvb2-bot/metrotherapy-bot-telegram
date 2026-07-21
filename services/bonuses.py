from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfoNotFoundError

from core.time_utils import today_tz, tzinfo, utc_now
from services.db import db


@dataclass
class BonusStats:
    # Field names are retained for handler compatibility. Values now represent
    # practice tokens, not calendar days.
    earned_days: int
    used_days: int
    remaining_days: int


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


def add_grant(user_id: int, days: int, *, source: str, related_user_id: int | None = None) -> None:
    """Record a legacy bonus projection."""
    days = int(days)
    if days <= 0:
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO bonus_grants(user_id, days, source, related_user_id, granted_at_utc) VALUES(?,?,?,?,?)",
            (
                int(user_id),
                days,
                (source or "").strip() or "referral",
                int(related_user_id) if related_user_id is not None else None,
                utc_now().replace(microsecond=0).isoformat(),
            ),
        )


def _canonical_reward_stats(user_id: int) -> BonusStats | None:
    try:
        with db() as conn:
            earned_row = conn.execute(
                """
                SELECT COALESCE(SUM(COALESCE(tokens_granted, days)),0) AS earned
                FROM bonus_grants
                WHERE user_id=? AND reward_key IS NOT NULL AND reward_key<>''
                  AND COALESCE(reward_status,'active')<>'revoked'
                """.strip(),
                (int(user_id),),
            ).fetchone()
            lot_row = conn.execute(
                """
                SELECT COALESCE(SUM(available_tokens),0) AS available,
                       COALESCE(SUM(reserved_tokens),0) AS reserved,
                       COALESCE(SUM(used_tokens),0) AS used
                FROM practice_token_lots
                WHERE user_id=? AND lot_key LIKE 'reward:%' AND refunded_tokens=0
                """.strip(),
                (int(user_id),),
            ).fetchone()
    except sqlite3.Error as exc:
        logging.getLogger(__name__).debug("Canonical reward stats unavailable: %s", exc)
        return None

    earned = max(0, int(_row_value(earned_row, "earned", 0, 0) or 0))
    available = max(0, int(_row_value(lot_row, "available", 0, 0) or 0))
    reserved = max(0, int(_row_value(lot_row, "reserved", 1, 0) or 0))
    lot_used = max(0, int(_row_value(lot_row, "used", 2, 0) or 0))
    remaining = min(earned, available + reserved)
    used = min(earned, max(lot_used, earned - remaining))
    return BonusStats(earned_days=earned, used_days=used, remaining_days=remaining)


def _legacy_stats(user_id: int) -> BonusStats:
    today_local = today_tz()
    earned = 0
    used = 0
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT days, granted_at_utc FROM bonus_grants WHERE user_id=? ORDER BY granted_at_utc ASC",
                (int(user_id),),
            ).fetchall()
    except sqlite3.Error:
        logging.getLogger(__name__).exception("Failed to read bonus grants")
        rows = []

    for row in rows:
        try:
            amount = int(_row_value(row, "days", 0, 0) or 0)
        except (TypeError, ValueError, KeyError):
            logging.getLogger(__name__).debug("Invalid bonus amount", exc_info=True)
            continue
        if amount <= 0:
            continue
        timestamp_raw = _row_value(row, "granted_at_utc", 1)
        try:
            timestamp = datetime.fromisoformat(str(timestamp_raw))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            logging.getLogger(__name__).warning(
                "Invalid bonus timestamp excluded user_id=%s value=%r",
                int(user_id),
                timestamp_raw,
            )
            continue
        earned += amount
        try:
            local_date = timestamp.astimezone(tzinfo()).date()
        except (ValueError, ZoneInfoNotFoundError):
            local_date = timestamp.date()
        used += min(amount, max(0, (today_local - local_date).days))

    used = min(earned, used)
    return BonusStats(earned_days=earned, used_days=used, remaining_days=max(0, earned - used))


def get_stats(user_id: int) -> BonusStats:
    """Return earned, consumed and remaining bonus practices."""
    canonical = _canonical_reward_stats(int(user_id))
    return canonical if canonical is not None else _legacy_stats(int(user_id))


def compute_bonus_stats(user_id: int) -> BonusStats:
    return get_stats(user_id)


def paid_referrals_count(user_id: int) -> int:
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS n FROM referrals WHERE referrer_id=? AND paid_at IS NOT NULL",
                (int(user_id),),
            ).fetchone()
        return int(_row_value(row, "n", 0, 0) or 0)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading referral stats")
        return 0


def paid_referrals_days_granted(user_id: int) -> int:
    try:
        with db() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(days),0) AS d FROM bonus_grants
                WHERE user_id=? AND source='referral'
                  AND COALESCE(reward_status,'active')<>'revoked'
                """.strip(),
                (int(user_id),),
            ).fetchone()
        return int(_row_value(row, "d", 0, 0) or 0)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading referral bonus stats")
        return 0


def gift_grants_count(user_id: int) -> int:
    try:
        with db() as conn:
            row = conn.execute(
                """
                SELECT COUNT(1) AS n FROM bonus_grants
                WHERE user_id=? AND source='gift'
                  AND COALESCE(reward_status,'active')<>'revoked'
                """.strip(),
                (int(user_id),),
            ).fetchone()
        return int(_row_value(row, "n", 0, 0) or 0)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading gift bonus stats")
        return 0


def gift_days_granted(user_id: int) -> int:
    try:
        with db() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(days),0) AS d FROM bonus_grants
                WHERE user_id=? AND source='gift'
                  AND COALESCE(reward_status,'active')<>'revoked'
                """.strip(),
                (int(user_id),),
            ).fetchone()
        return int(_row_value(row, "d", 0, 0) or 0)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading gift bonus stats")
        return 0
