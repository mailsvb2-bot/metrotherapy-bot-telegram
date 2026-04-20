from __future__ import annotations
import sqlite3


from datetime import datetime, timezone

from services.db import db


def _today_utc_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def users_joined_today_count() -> int:
    """Сколько пользователей зашло сегодня (по UTC)."""
    today = _today_utc_date()
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(1) AS n FROM users WHERE COALESCE(substr(joined_at,1,10),'') = ?",
            (today,),
        ).fetchone()
    # sqlite3.Row не поддерживает .get()
    if not row:
        return 0
    return int(row["n"] or 0)


def users_joined_today(limit: int = 30) -> list[dict]:
    """Список пользователей, зашедших сегодня (UTC)."""
    today = _today_utc_date()
    with db() as conn:
        rows = conn.execute(
            "SELECT user_id, joined_at, username, first_name FROM users "
            "WHERE COALESCE(substr(joined_at,1,10),'') = ? "
            "ORDER BY joined_at DESC LIMIT ?",
            (today, int(limit)),
        ).fetchall()
    out = []
    for r in rows or []:
        out.append(
            {
                "user_id": int(r["user_id"]),
                "joined_at": r["joined_at"],
                "username": r["username"],
                "first_name": r["first_name"],
            }
        )
    return out


def user_card(user_id: int) -> dict:
    """Карточка пользователя для админки (минимум, но полезно)."""
    user_id = int(user_id)
    with db() as conn:
        u = conn.execute(
            "SELECT user_id, joined_at, username, first_name, work_time, home_time FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()

        sub = conn.execute(
            "SELECT scope, plan_type, total_morning, total_evening, used_morning, used_evening, status, started_at, paid_at FROM subscriptions WHERE user_id=?",
            (user_id,),
        ).fetchone()

        demo = conn.execute(
            "SELECT kind, sent_at_utc, ack_at_utc FROM demo_events WHERE user_id=? ORDER BY sent_at_utc DESC",
            (user_id,),
        ).fetchall()

        w = conn.execute(
            "SELECT city, lat, lon, updated_at FROM weather_prefs WHERE user_id=?",
            (user_id,),
        ).fetchone()

        ref = conn.execute(
            "SELECT referrer_id, reward_given, reward_days FROM referrals WHERE referred_id=?",
            (user_id,),
        ).fetchone()

        invited = conn.execute(
            "SELECT COUNT(1) AS n FROM referrals WHERE referrer_id=?",
            (user_id,),
        ).fetchone()

        beh = conn.execute(
            "SELECT ema_delta_ms, ema_absdev_ms, profile, updated_at FROM user_behavior WHERE user_id=?",
            (user_id,),
        ).fetchone()

        micro = conn.execute(
            "SELECT q_key, answer, ts FROM micro_answers WHERE user_id=? ORDER BY ts DESC LIMIT 10",
            (user_id,),
        ).fetchall()

    return {
        "user": dict(u) if u else None,
        "sub": dict(sub) if sub else None,
        "demo": [dict(r) for r in (demo or [])],
        "weather": dict(w) if w else None,
        "ref": dict(ref) if ref else None,
        "invited_count": int(invited["n"] or 0) if invited else 0,
        "behavior": dict(beh) if beh else None,
        "micro": [dict(r) for r in (micro or [])],
    }
