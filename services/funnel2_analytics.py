from __future__ import annotations


import json
from datetime import datetime, timezone, timedelta

from services.db import get_db
from services.funnel2 import SC_DEMO_NOPAY_24H, SC_EXPIRED_RETURN_3D


def _parse(dt_iso: str) -> datetime:
    dt = datetime.fromisoformat(dt_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _sent_count_sql(time_min: str | None, time_max: str | None) -> tuple[str, list[str | None]]:
    if time_min and time_max:
        return (
            "SELECT scenario_key, COUNT(1) AS n FROM funnel_events WHERE sent_at_utc >= ? AND sent_at_utc < ? GROUP BY scenario_key",
            [time_min, time_max],
        )
    if time_min:
        return (
            "SELECT scenario_key, COUNT(1) AS n FROM funnel_events WHERE sent_at_utc >= ? GROUP BY scenario_key",
            [time_min],
        )
    if time_max:
        return (
            "SELECT scenario_key, COUNT(1) AS n FROM funnel_events WHERE sent_at_utc < ? GROUP BY scenario_key",
            [time_max],
        )
    return ("SELECT scenario_key, COUNT(1) AS n FROM funnel_events GROUP BY scenario_key", [])


def _conversion_count_sql(time_min: str | None, time_max: str | None, scenario_key: str) -> tuple[str, list[str | None]]:
    if time_min and time_max:
        return (
            """
            SELECT COUNT(DISTINCT p.user_id) AS n
            FROM funnel_events f
            JOIN payments p ON p.user_id=f.user_id
            WHERE f.scenario_key=?
              AND p.payload NOT LIKE 'gift:%'
              AND (p.created_at IS NOT NULL)
              AND p.created_at >= f.sent_at_utc
              AND f.sent_at_utc >= ?
              AND f.sent_at_utc < ?
            """.strip(),
            [scenario_key, time_min, time_max],
        )
    if time_min:
        return (
            """
            SELECT COUNT(DISTINCT p.user_id) AS n
            FROM funnel_events f
            JOIN payments p ON p.user_id=f.user_id
            WHERE f.scenario_key=?
              AND p.payload NOT LIKE 'gift:%'
              AND (p.created_at IS NOT NULL)
              AND p.created_at >= f.sent_at_utc
              AND f.sent_at_utc >= ?
            """.strip(),
            [scenario_key, time_min],
        )
    if time_max:
        return (
            """
            SELECT COUNT(DISTINCT p.user_id) AS n
            FROM funnel_events f
            JOIN payments p ON p.user_id=f.user_id
            WHERE f.scenario_key=?
              AND p.payload NOT LIKE 'gift:%'
              AND (p.created_at IS NOT NULL)
              AND p.created_at >= f.sent_at_utc
              AND f.sent_at_utc < ?
            """.strip(),
            [scenario_key, time_max],
        )
    return (
        """
        SELECT COUNT(DISTINCT p.user_id) AS n
        FROM funnel_events f
        JOIN payments p ON p.user_id=f.user_id
        WHERE f.scenario_key=?
          AND p.payload NOT LIKE 'gift:%'
          AND (p.created_at IS NOT NULL)
          AND p.created_at >= f.sent_at_utc
        """.strip(),
        [scenario_key],
    )


def scenario_counts(time_min: str | None = None, time_max: str | None = None) -> dict:
    """Отчёт по сценариям Funnel 2.0.

    Возвращает counts по отправкам и конверсиям (оплата после сценария).
    """
    with get_db() as conn:
        sent_sql, sent_params = _sent_count_sql(time_min, time_max)
        sent_rows = conn.execute(
            sent_sql,
            sent_params,
        ).fetchall()

        sent = {r["scenario_key"]: int(r["n"]) for r in sent_rows}

        # Конверсия: оплатил после sent_at_utc (не подарок)
        conv = {}
        for sc in (SC_DEMO_NOPAY_24H, SC_EXPIRED_RETURN_3D):
            conv_sql, conv_params = _conversion_count_sql(time_min, time_max, sc)
            rows = conn.execute(conv_sql, conv_params).fetchone()
            conv[sc] = int(rows["n"] or 0) if rows else 0

    out = {"sent": sent, "converted": conv}

    def pct(a: int, b: int) -> int | None:
        if not b:
            return None
        return int(round(a * 100 / b))

    out["pct"] = {sc: pct(conv.get(sc, 0), sent.get(sc, 0)) for sc in conv}
    return out


def format_report(title: str, time_min: str | None, time_max: str | None) -> str:
    rep = scenario_counts(time_min, time_max)

    def line(sc: str, name: str) -> str:
        s = int(rep["sent"].get(sc, 0))
        c = int(rep["converted"].get(sc, 0))
        p = rep["pct"].get(sc)
        tail = f" ({p}%)" if p is not None else ""
        return f"— {name}: sent={s} → paid={c}{tail}"

    return (
        f"🧲 Автоворонка 2.0 — сценарии\n{title}\n\n"
        + line(SC_DEMO_NOPAY_24H, "Demo: не оплатил 24ч")
        + "\n"
        + line(SC_EXPIRED_RETURN_3D, "Expired: возврат через 3д")
        + "\n\n"
        "ℹ️ paid считается как успешная оплата после отправки сценария (не gift)."
    )
