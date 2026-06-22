from __future__ import annotations
import logging
import sqlite3


"""Referral bonuses bookkeeping (days).

Установки:
- Бонусы начисляются только за оплативших приглашённых.
- Миграции БД только "вперёд".
- Доказуемость: фиксируем каждое начисление.

Пользовательские метрики:
- начислено: сумма всех бонус-дней.
- израсходовано: сколько календарных дней прошло с момента начисления
  (по каждому начислению отдельно), ограниченно размером начисления.
- остаток: начислено - израсходовано.

Примечание:
Эта модель не требует отдельного "списания" и соответствует UX,
где бонус-дни дают доступ во времени.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfoNotFoundError
from core.time_utils import utc_now, today_tz, tzinfo
from typing import Any

from services.db import db


@dataclass
class BonusStats:
    earned_days: int
    used_days: int
    remaining_days: int
def add_grant(user_id: int, days: int, *, source: str, related_user_id: int | None = None) -> None:
    """Фиксирует начисление бонус-дней."""
    days = int(days)
    if days <= 0:
        return
    with db() as conn:
        conn.execute(
            "INSERT INTO bonus_grants(user_id, days, source, related_user_id, granted_at_utc) VALUES(?,?,?,?,?)",
            (int(user_id), days, (source or "").strip() or "referral", int(related_user_id) if related_user_id is not None else None, utc_now().replace(microsecond=0).isoformat()),
        )


def get_stats(user_id: int) -> BonusStats:
    """Возвращает (начислено/израсходовано/остаток) в днях."""
    now_utc = utc_now().replace(microsecond=0)
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

    for r in rows:
        try:
            d = int(r[0] if not hasattr(r, "keys") else r["days"])
        except (TypeError, ValueError, KeyError):
            logging.getLogger(__name__).debug("Invalid bonus days value", exc_info=True)
            d = 0
        if d <= 0:
            continue
        earned += d
        ts_raw = r[1] if not hasattr(r, "keys") else r["granted_at_utc"]
        try:
            ts = datetime.fromisoformat(str(ts_raw))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            ts = now_utc
        # consumed calendar days since grant
        try:
            ts_local_date = ts.astimezone(tzinfo()).date()
        except (ValueError, ZoneInfoNotFoundError):
            # Крайний случай: если TZ недоступен/битый — используем UTC-даты,
            # чтобы не ломать UX (но это видно по логам настроек).
            ts_local_date = ts.date()
        delta_days = (today_local - ts_local_date).days
        if delta_days < 0:
            delta_days = 0
        used += min(d, delta_days)

    used = min(earned, used)
    rem = max(0, earned - used)
    return BonusStats(earned_days=earned, used_days=used, remaining_days=rem)


# Backward-compatible alias used by handlers.
# Установка A (контракты важнее кода): не ломаем существующие импорты.
def compute_bonus_stats(user_id: int) -> BonusStats:
    return get_stats(user_id)


def paid_referrals_count(user_id: int) -> int:
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS n FROM referrals WHERE referrer_id=? AND paid_at IS NOT NULL",
                (int(user_id),),
            ).fetchone()
        return int((row["n"] if row and hasattr(row, "keys") else row[0]) or 0)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading bonuses stats")
        return 0


def paid_referrals_days_granted(user_id: int) -> int:
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(days),0) AS d FROM bonus_grants WHERE user_id=?",
                (int(user_id),),
            ).fetchone()
        return int((row["d"] if row and hasattr(row, "keys") else row[0]) or 0)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading bonuses stats")
        return 0


def gift_grants_count(user_id: int) -> int:
    """Сколько раз пользователь получал бонусы за подарки."""
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT COUNT(1) AS n FROM bonus_grants WHERE user_id=? AND source='gift'",
                (int(user_id),),
            ).fetchone()
        return int((row["n"] if row and hasattr(row, "keys") else row[0]) or 0)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading bonuses stats")
        return 0


def gift_days_granted(user_id: int) -> int:
    """Сколько бонус-дней начислено за подарки."""
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(days),0) AS d FROM bonus_grants WHERE user_id=? AND source='gift'",
                (int(user_id),),
            ).fetchone()
        return int((row["d"] if row and hasattr(row, "keys") else row[0]) or 0)
    except sqlite3.Error:
        logging.getLogger(__name__).exception("DB error while reading bonuses stats")
        return 0
