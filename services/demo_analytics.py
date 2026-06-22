from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from services.db import db
from services.events import log_event


def record_demo_sent(user_id: int, kind: str, message_id: int, sent_at_utc: str, voice_duration_sec: int | None):
    with db() as conn:
        conn.execute(
            """
            INSERT INTO demo_events(user_id, kind, message_id, sent_at_utc, voice_duration_sec)
            VALUES(?,?,?,?,?)
            """,
            (int(user_id), kind, int(message_id), sent_at_utc, voice_duration_sec),
        )
    log_event(user_id, "demo_sent", {"kind": kind, "message_id": message_id, "duration": voice_duration_sec})


def record_demo_ack(user_id: int, kind: str, message_id: int, ack_at_utc: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT id, sent_at_utc, ack_at_utc FROM demo_events WHERE user_id=? AND kind=? AND message_id=?",
            (int(user_id), kind, int(message_id)),
        ).fetchone()

        if not row:
            return False
        if row["ack_at_utc"]:
            return True

        sent = datetime.fromisoformat(row["sent_at_utc"])
        ack = datetime.fromisoformat(ack_at_utc)
        delay = int(max(0, (ack - sent).total_seconds()))

        conn.execute(
            "UPDATE demo_events SET ack_at_utc=?, ack_delay_sec=? WHERE id=?",
            (ack_at_utc, delay, int(row["id"])),
        )

    log_event(user_id, "demo_ack", {"kind": kind, "message_id": message_id, "delay_sec": delay})
    return True


def demo_sent_kinds(user_id: int) -> set[str]:
    """Какие виды демо уже отправлялись пользователю.

    Используем для ограничения бесплатных повторных отправок.
    """
    with db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT kind FROM demo_events WHERE user_id=?",
            (int(user_id),),
        ).fetchall()
    out: set[str] = set()
    for r in rows:
        if not r:
            continue
        k = (r["kind"] if "kind" in r.keys() else None)
        if k:
            out.add(str(k))
    return out


def demo_summary() -> dict:
    with db() as conn:
        sent_work = conn.execute("SELECT COUNT(*) c FROM demo_events WHERE kind='work'").fetchone()["c"]
        sent_home = conn.execute("SELECT COUNT(*) c FROM demo_events WHERE kind='home'").fetchone()["c"]
        ack_work = conn.execute("SELECT COUNT(*) c FROM demo_events WHERE kind='work' AND ack_at_utc IS NOT NULL").fetchone()["c"]
        ack_home = conn.execute("SELECT COUNT(*) c FROM demo_events WHERE kind='home' AND ack_at_utc IS NOT NULL").fetchone()["c"]

        both = conn.execute("""
        SELECT COUNT(*) c FROM (
            SELECT user_id FROM demo_events
            WHERE ack_at_utc IS NOT NULL
            GROUP BY user_id
            HAVING SUM(CASE WHEN kind='work' THEN 1 ELSE 0 END) > 0
               AND SUM(CASE WHEN kind='home' THEN 1 ELSE 0 END) > 0
        )
        """).fetchone()["c"]

        avg_delay = conn.execute("SELECT AVG(ack_delay_sec) a FROM demo_events WHERE ack_delay_sec IS NOT NULL").fetchone()["a"]

    return {
        "sent_work": int(sent_work),
        "sent_home": int(sent_home),
        "ack_work": int(ack_work),
        "ack_home": int(ack_home),
        "both_acked_users": int(both),
        "avg_ack_delay_sec": int(avg_delay) if avg_delay is not None else None,
    }


def demo_summary_for_range(start_utc: str, end_utc: str) -> dict:
    """Краткая статистика по демо за диапазон (sent_at_utc в [start,end))."""

    with db() as conn:
        sent_work = conn.execute(
            "SELECT COUNT(*) c FROM demo_events WHERE kind='work' AND sent_at_utc>=? AND sent_at_utc<?",
            (start_utc, end_utc),
        ).fetchone()["c"]
        sent_home = conn.execute(
            "SELECT COUNT(*) c FROM demo_events WHERE kind='home' AND sent_at_utc>=? AND sent_at_utc<?",
            (start_utc, end_utc),
        ).fetchone()["c"]

        ack_work = conn.execute(
            "SELECT COUNT(*) c FROM demo_events WHERE kind='work' AND ack_at_utc IS NOT NULL AND sent_at_utc>=? AND sent_at_utc<?",
            (start_utc, end_utc),
        ).fetchone()["c"]
        ack_home = conn.execute(
            "SELECT COUNT(*) c FROM demo_events WHERE kind='home' AND ack_at_utc IS NOT NULL AND sent_at_utc>=? AND sent_at_utc<?",
            (start_utc, end_utc),
        ).fetchone()["c"]

        uniq_users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM demo_events WHERE sent_at_utc>=? AND sent_at_utc<?",
            (start_utc, end_utc),
        ).fetchone()["c"]

        dur_sum = conn.execute(
            "SELECT SUM(COALESCE(voice_duration_sec,0)) s FROM demo_events WHERE sent_at_utc>=? AND sent_at_utc<?",
            (start_utc, end_utc),
        ).fetchone()["s"]

    return {
        "uniq_users": int(uniq_users),
        "sent_work": int(sent_work),
        "sent_home": int(sent_home),
        "ack_work": int(ack_work),
        "ack_home": int(ack_home),
        "duration_sum_sec": int(dur_sum) if dur_sum is not None else 0,
    }


def demo_listen_distribution() -> dict:
    """Сколько пользователей отметили 0/1/2 демо (по ack_at_utc и kind)."""

    with db() as conn:
        rows = conn.execute(
            """
            SELECT user_id, COUNT(DISTINCT kind) k
            FROM demo_events
            WHERE ack_at_utc IS NOT NULL
            GROUP BY user_id
            """
        ).fetchall()

        one = 0
        two = 0
        for r in rows:
            k = int(r["k"])
            if k >= 2:
                two += 1
            elif k == 1:
                one += 1

        sent_users = conn.execute("SELECT COUNT(DISTINCT user_id) c FROM demo_events").fetchone()["c"]
        ack_users = conn.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM demo_events WHERE ack_at_utc IS NOT NULL"
        ).fetchone()["c"]

    return {
        "sent_users": int(sent_users),
        "acked_users": int(ack_users),
        "acked_one": int(one),
        "acked_two": int(two),
    }


def demo_user_breakdown(limit: int = 25) -> list[dict]:
    """Топ пользователей по сумме длительностей демо (duration по метаданным аудио)."""

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 25
    limit = max(1, min(limit, 100))

    with db() as conn:
        rows = conn.execute(
            """
            SELECT user_id,
                   COUNT(*) sent,
                   SUM(CASE WHEN ack_at_utc IS NOT NULL THEN 1 ELSE 0 END) ack,
                   SUM(COALESCE(voice_duration_sec,0)) dur,
                   MIN(sent_at_utc) first_sent,
                   MAX(sent_at_utc) last_sent
            FROM demo_events
            GROUP BY user_id
            ORDER BY dur DESC, sent DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    out = []
    for r in rows:
        out.append(
            {
                "user_id": int(r["user_id"]),
                "sent": int(r["sent"]),
                "ack": int(r["ack"]),
                "dur_sec": int(r["dur"] or 0),
                "first_sent": r["first_sent"],
                "last_sent": r["last_sent"],
            }
        )
    return out


def today_range_utc(tz_name: str) -> tuple[str, str]:
    tz = ZoneInfo(tz_name)
    now_tz = datetime.now(tz)
    start_tz = now_tz.replace(hour=0, minute=0, second=0, microsecond=0)
    end_tz = start_tz + timedelta(days=1)
    start_utc = start_tz.astimezone(ZoneInfo("UTC")).isoformat()
    end_utc = end_tz.astimezone(ZoneInfo("UTC")).isoformat()
    return start_utc, end_utc
