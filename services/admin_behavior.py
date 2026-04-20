from __future__ import annotations


from datetime import datetime, timedelta, timezone

from services.db import db
from services.behavior import behavior_distribution


def behavior_report(days: int = 7) -> dict:
    """Returns aggregated behavioral analytics for admin panel."""

    dist = behavior_distribution()
    total = sum(dist.values()) or 0
    pct = {k: int(round(v * 100 / total)) if total else 0 for k, v in dist.items()}

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=int(days))).replace(microsecond=0).isoformat()

    with db() as conn:
        # interaction intensity
        irows = conn.execute(
            """
            SELECT kind, COUNT(*) AS cnt
            FROM interaction_log
            WHERE ts >= ?
            GROUP BY kind
            """,
            (since,),
        ).fetchall()
        interactions = {r["kind"]: int(r["cnt"] or 0) for r in irows}

        # micro answers
        a = conn.execute(
            "SELECT COUNT(*) FROM micro_answers WHERE ts >= ?",
            (since,),
        ).fetchone()[0]

        # bricks
        b = conn.execute(
            "SELECT COUNT(*) FROM user_bricks WHERE ts >= ?",
            (since,),
        ).fetchone()[0]

        # rough "pre-purchase" signal: users who opened tariffs (event name)
        opened = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM events WHERE (event='sub_menu_open' OR name='sub_menu_open') AND (ts >= ? OR created_at >= ?)",
            (since, since),
        ).fetchone()[0]

    return {
        "dist": dist,
        "pct": pct,
        "total": total,
        "since": since,
        "interactions": interactions,
        "micro_answers": int(a or 0),
        "bricks": int(b or 0),
        "sub_menu_open_users": int(opened or 0),
    }


def user_behavior_card(user_id: int) -> dict:
    with db() as conn:
        b = conn.execute(
            "SELECT ema_delta_ms, ema_absdev_ms, profile, updated_at FROM user_behavior WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        f = conn.execute(
            "SELECT stage, variant, updated_at FROM user_funnel WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        last = conn.execute(
            "SELECT kind, key, delta_ms, ts FROM interaction_log WHERE user_id=? ORDER BY ts DESC LIMIT 10",
            (int(user_id),),
        ).fetchall()
        bricks = conn.execute(
            "SELECT brick_key, context, ts FROM user_bricks WHERE user_id=? ORDER BY ts DESC LIMIT 15",
            (int(user_id),),
        ).fetchall()

    return {
        "user_id": int(user_id),
        "behavior": dict(b) if b else None,
        "funnel": dict(f) if f else None,
        "last_interactions": [dict(r) for r in last],
        "last_bricks": [dict(r) for r in bricks],
    }
