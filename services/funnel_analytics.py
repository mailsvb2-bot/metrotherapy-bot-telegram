from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services.commercial_funnel_contract import COMMERCIAL_STEP_EVENT_NAMES
from services.db import db
from services.funnel_analytics_indexes import (
    COMMERCIAL_FUNNEL_EVENT_NAMES,
    COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL,
)


DEFAULT_STEPS: list[str] = [
    "demo_sent",
    "demo_ack",
    "funnel_nudge_sent",
    "funnel_offer_sent",
    "funnel_deadline_sent",
    "funnel_lastcall_sent",
    "view_tariffs",
    "invoice_created",
    "payment_started",
    "invoice_paid",
    "payment_success",
    "successful_payment",
    "sub_paid",
]

_MONEY_CHAIN: tuple[tuple[str, str], ...] = (
    ("demo", "Получили демо"),
    ("listened", "Подтвердили/прослушали демо"),
    ("offer", "Открыли предложение/тарифы"),
    ("checkout", "Создан checkout"),
    ("paid", "Оплатили пакет"),
)


def _sql_literals(values: tuple[str, ...]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


def _event_count_sql(start_utc: str | None, end_utc: str | None) -> tuple[str, str]:
    if start_utc and end_utc:
        return (
            "SELECT COUNT(DISTINCT user_id) AS cnt FROM events WHERE name=? AND created_at >= ? AND created_at < ?",
            "both",
        )
    if start_utc:
        return (
            "SELECT COUNT(DISTINCT user_id) AS cnt FROM events WHERE name=? AND created_at >= ?",
            "start",
        )
    if end_utc:
        return (
            "SELECT COUNT(DISTINCT user_id) AS cnt FROM events WHERE name=? AND created_at < ?",
            "end",
        )
    return ("SELECT COUNT(DISTINCT user_id) AS cnt FROM events WHERE name=?", "none")


def _event_rows_sql(start_utc: str | None, end_utc: str | None) -> tuple[str, str]:
    if start_utc and end_utc:
        return (
            "SELECT user_id, name, meta, created_at FROM events WHERE name=? AND created_at >= ? AND created_at < ? ORDER BY created_at ASC",
            "both",
        )
    if start_utc:
        return (
            "SELECT user_id, name, meta, created_at FROM events WHERE name=? AND created_at >= ? ORDER BY created_at ASC",
            "start",
        )
    if end_utc:
        return (
            "SELECT user_id, name, meta, created_at FROM events WHERE name=? AND created_at < ? ORDER BY created_at ASC",
            "end",
        )
    return ("SELECT user_id, name, meta, created_at FROM events WHERE name=? ORDER BY created_at ASC", "none")


def _date_params(mode: str, name: str, start_utc: str | None, end_utc: str | None) -> tuple[Any, ...]:
    if mode == "both":
        return (name, start_utc, end_utc)
    if mode == "start":
        return (name, start_utc)
    if mode == "end":
        return (name, end_utc)
    return (name,)


def _row_value(row: Any, key: str, index: int) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        try:
            return row[index]
        except (TypeError, IndexError):
            return None


def _commercial_counts(
    names: list[str],
    start_utc: str | None,
    end_utc: str | None,
) -> dict[str, int]:
    placeholders = ", ".join("?" for _ in names)
    sql = (
        "SELECT name, COUNT(DISTINCT user_id) AS cnt "
        "FROM events "
        f"WHERE name IN ({COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL}) "
        f"AND name IN ({placeholders})"
    )
    params: list[Any] = list(names)
    if start_utc:
        sql += " AND created_at >= ?"
        params.append(start_utc)
    if end_utc:
        sql += " AND created_at < ?"
        params.append(end_utc)
    sql += " GROUP BY name"

    out = {name: 0 for name in names}
    with db() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    for row in rows:
        name = str(_row_value(row, "name", 0) or "")
        if name in out:
            try:
                out[name] = int(_row_value(row, "cnt", 1) or 0)
            except (TypeError, ValueError):
                out[name] = 0
    return out


def _counts(names: list[str], start_utc: str | None = None, end_utc: str | None = None) -> dict[str, int]:
    if not names:
        return {}

    indexed_names = set(COMMERCIAL_FUNNEL_EVENT_NAMES)
    if set(names).issubset(indexed_names):
        return _commercial_counts(names, start_utc, end_utc)

    res: dict[str, int] = {n: 0 for n in names}
    sql, mode = _event_count_sql(start_utc, end_utc)
    with db() as conn:
        for name in names:
            row = conn.execute(sql, _date_params(mode, name, start_utc, end_utc)).fetchone()
            try:
                res[name] = int(_row_value(row, "cnt", 0) or 0) if row else 0
            except (TypeError, ValueError):
                logging.getLogger(__name__).exception("Bad row in funnel counts")
                res[name] = 0
    return res


def _paid_user_count(start_utc: str | None = None, end_utc: str | None = None) -> int:
    sql = "SELECT COUNT(DISTINCT user_id) AS cnt FROM payment_token_grants WHERE 1=1"
    params: list[Any] = []
    if start_utc:
        sql += " AND created_at >= ?"
        params.append(start_utc)
    if end_utc:
        sql += " AND created_at < ?"
        params.append(end_utc)
    with db() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    try:
        return int(_row_value(row, "cnt", 0) or 0) if row else 0
    except (TypeError, ValueError):
        return 0


def _strict_money_counts(
    start_utc: str | None,
    end_utc: str | None,
) -> dict[str, int]:
    """Count a real ordered demo cohort without rescanning the full events table.

    Each later milestone must occur after the previous milestone for the same user.
    Demo-listen evidence comes from both commercial telemetry and the canonical
    audio timeline: older live ``mood:done`` / cross-messenger confirmations
    already wrote ``manual_confirmed`` there even when they did not emit a
    ``demo_ack`` analytics event. Paid state comes from payment_token_grants, not
    from analytics events. The production partial index is usable because every
    events access explicitly carries the complete commercial-event predicate.
    """

    demo_names = _sql_literals(COMMERCIAL_STEP_EVENT_NAMES["demo"])
    listened_names = _sql_literals(COMMERCIAL_STEP_EVENT_NAMES["listened"])
    offer_names = _sql_literals(COMMERCIAL_STEP_EVENT_NAMES["offer"])
    checkout_names = _sql_literals(COMMERCIAL_STEP_EVENT_NAMES["checkout"])

    demo_where = [
        f"name IN ({COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL})",
        f"name IN ({demo_names})",
    ]
    demo_params: list[Any] = []
    if start_utc:
        demo_where.append("created_at >= ?")
        demo_params.append(start_utc)
    if end_utc:
        demo_where.append("created_at < ?")
        demo_params.append(end_utc)

    listened_upper = ""
    offer_upper = ""
    checkout_upper = ""
    ordered_params: list[Any] = list(demo_params)
    if end_utc:
        listened_upper = " AND le.listened_at < ?"
        offer_upper = " AND e.created_at < ?"
        checkout_upper = " AND e.created_at < ?"
        ordered_params.extend([end_utc, end_utc, end_utc])

    paid_where = ["1=1"]
    if start_utc:
        paid_where.append("created_at >= ?")
        ordered_params.append(start_utc)
    if end_utc:
        paid_where.append("created_at < ?")
        ordered_params.append(end_utc)

    sql = f"""
        WITH demos AS (
            SELECT user_id, MIN(created_at) AS demo_at
            FROM events
            WHERE {' AND '.join(demo_where)}
            GROUP BY user_id
        ),
        listen_evidence AS (
            SELECT user_id, created_at AS listened_at
            FROM events
            WHERE name IN ({COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL})
              AND name IN ({listened_names})
            UNION ALL
            SELECT user_id, created_at AS listened_at
            FROM user_audio_timeline
            WHERE sequence_key='demo'
              AND event_type='manual_confirmed'
        ),
        listened AS (
            SELECT d.user_id, d.demo_at, MIN(le.listened_at) AS listened_at
            FROM demos AS d
            LEFT JOIN listen_evidence AS le
              ON le.user_id = d.user_id
             AND le.listened_at >= d.demo_at
             {listened_upper}
            GROUP BY d.user_id, d.demo_at
        ),
        offers AS (
            SELECT l.user_id, l.demo_at, l.listened_at, MIN(e.created_at) AS offer_at
            FROM listened AS l
            LEFT JOIN events AS e
              ON l.listened_at IS NOT NULL
             AND e.user_id = l.user_id
             AND e.name IN ({COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL})
             AND e.name IN ({offer_names})
             AND e.created_at >= l.listened_at
             {offer_upper}
            GROUP BY l.user_id, l.demo_at, l.listened_at
        ),
        checkouts AS (
            SELECT o.user_id, o.demo_at, o.listened_at, o.offer_at,
                   MIN(e.created_at) AS checkout_at
            FROM offers AS o
            LEFT JOIN events AS e
              ON o.offer_at IS NOT NULL
             AND e.user_id = o.user_id
             AND e.name IN ({COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL})
             AND e.name IN ({checkout_names})
             AND e.created_at >= o.offer_at
             {checkout_upper}
            GROUP BY o.user_id, o.demo_at, o.listened_at, o.offer_at
        ),
        paid_steps AS (
            SELECT user_id, MAX(created_at) AS paid_at
            FROM payment_token_grants
            WHERE {' AND '.join(paid_where)}
            GROUP BY user_id
        )
        SELECT
            COUNT(c.user_id) AS demo_users,
            COALESCE(SUM(CASE WHEN c.listened_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS listened_users,
            COALESCE(SUM(CASE WHEN c.offer_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS offer_users,
            COALESCE(SUM(CASE WHEN c.checkout_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS checkout_users,
            COALESCE(SUM(CASE WHEN c.checkout_at IS NOT NULL AND p.paid_at >= c.checkout_at THEN 1 ELSE 0 END), 0) AS paid_users,
            (SELECT COUNT(*) FROM paid_steps) AS paid_total
        FROM checkouts AS c
        LEFT JOIN paid_steps AS p ON p.user_id = c.user_id
    """.strip()

    with db() as conn:
        row = conn.execute(sql, tuple(ordered_params)).fetchone()

    keys = ("demo", "listened", "offer", "checkout", "paid", "paid_total")
    out: dict[str, int] = {}
    for index, key in enumerate(keys):
        try:
            out[key] = int(_row_value(row, key if key == "paid_total" else f"{key}_users", index) or 0)
        except (TypeError, ValueError):
            out[key] = 0
    return out


def _rate_chain(points: list[tuple[str, int]]) -> list[dict[str, Any]]:
    rates: list[dict[str, Any]] = []
    prev: int | None = None
    for name, value in points:
        if prev is None:
            pct = None
            dropped = None
        else:
            pct = (value / prev * 100.0) if prev > 0 else 0.0
            dropped = max(prev - value, 0)
        rates.append(
            {
                "step": name,
                "users": value,
                "from_prev_pct": round(pct, 1) if pct is not None else None,
                "dropped_from_prev": dropped,
            }
        )
        prev = value
    return rates


def conversion_report(
    start_utc: str | None = None,
    end_utc: str | None = None,
    *,
    steps: list[str] | None = None,
) -> dict[str, Any]:
    """Read-only commercial conversion report over canonical telemetry/money state."""

    selected_steps = list(steps or DEFAULT_STEPS)
    counts = _counts(selected_steps, start_utc, end_utc)
    strict = _strict_money_counts(start_utc, end_utc)

    legacy_chain = _rate_chain(
        [
            ("demo_sent", int(counts.get("demo_sent", 0))),
            ("demo_ack", int(counts.get("demo_ack", 0))),
            ("view_tariffs", int(counts.get("view_tariffs", 0))),
            ("sub_paid", int(counts.get("sub_paid", 0))),
        ]
    )
    money_chain = _rate_chain(
        [
            ("demo", strict["demo"]),
            ("listened", strict["listened"]),
            ("offer", strict["offer"]),
            ("checkout", strict["checkout"]),
            ("paid", strict["paid"]),
        ]
    )

    return {
        "counts": counts,
        "chain": legacy_chain,
        "money_chain": money_chain,
        "paid_users": strict["paid_total"],
        "strict_paid_users": strict["paid"],
        "start_utc": start_utc,
        "end_utc": end_utc,
    }


def format_conversion_report(report: dict[str, Any], *, title: str = "за 30 дней") -> str:
    labels = dict(_MONEY_CHAIN)
    chain = list(report.get("money_chain") or [])
    lines = ["💰 Воронка до оплаты", title, ""]

    for item in chain:
        step = str(item.get("step") or "")
        users = int(item.get("users") or 0)
        pct = item.get("from_prev_pct")
        dropped = item.get("dropped_from_prev")
        suffix = ""
        if pct is not None:
            suffix = f" — {float(pct):.1f}% от прошлого шага"
            if dropped:
                suffix += f", потеря {int(dropped)}"
        lines.append(f"• {labels.get(step, step)}: {users}{suffix}")

    paid_total = int(report.get("paid_users") or 0)
    strict_paid = int(report.get("strict_paid_users") or 0)
    lines.extend(["", f"Всего плативших за пакеты в периоде: {paid_total}"])
    if paid_total != strict_paid:
        lines.append(
            f"Из строгой демо-цепочки дошли до оплаты: {strict_paid}; остальные оплаты пришли другим путём или из более ранней когорты."
        )

    leaks = [
        item
        for item in chain[1:]
        if item.get("from_prev_pct") is not None and int(item.get("dropped_from_prev") or 0) > 0
    ]
    if leaks:
        worst = min(leaks, key=lambda item: float(item.get("from_prev_pct") or 0.0))
        step = str(worst.get("step") or "")
        lines.extend(
            [
                "",
                (
                    "🎯 Самый слабый последовательный переход: "
                    f"{labels.get(step, step)} — {float(worst.get('from_prev_pct') or 0.0):.1f}%"
                ),
            ]
        )
    elif chain:
        lines.extend(["", "🎯 Явной точки потери в строгой демо-цепочке пока нет."])

    lines.extend(
        [
            "",
            "Строгая цепочка учитывает порядок событий одного пользователя; оплата подтверждается только payment_token_grants.",
        ]
    )
    return "\n".join(lines)


def _daypart_ru(hour: int) -> str:
    """Утро/день/вечер для аналитики."""
    h = int(hour) % 24
    if 5 <= h <= 11:
        return "утро"
    if 12 <= h <= 16:
        return "день"
    return "вечер"


def _commercial_event_rows(
    steps: list[str],
    start_utc: str | None,
    end_utc: str | None,
) -> list[Any]:
    placeholders = ", ".join("?" for _ in steps)
    sql = (
        "SELECT user_id, name, meta, created_at FROM events "
        f"WHERE name IN ({COMMERCIAL_FUNNEL_EVENT_PREDICATE_SQL}) "
        f"AND name IN ({placeholders})"
    )
    params: list[Any] = list(steps)
    if start_utc:
        sql += " AND created_at >= ?"
        params.append(start_utc)
    if end_utc:
        sql += " AND created_at < ?"
        params.append(end_utc)
    sql += " ORDER BY created_at ASC"
    with db() as conn:
        return list(conn.execute(sql, tuple(params)).fetchall())


def _breakdown_step(name: str) -> str | None:
    if name in COMMERCIAL_STEP_EVENT_NAMES["demo"]:
        return "demo_sent"
    if name in COMMERCIAL_STEP_EVENT_NAMES["listened"]:
        return "demo_ack"
    if name in COMMERCIAL_STEP_EVENT_NAMES["offer"]:
        return "view_tariffs"
    if name in COMMERCIAL_STEP_EVENT_NAMES["paid_event"]:
        return "sub_paid"
    return None


def conversion_breakdown(
    start_utc: str | None = None,
    end_utc: str | None = None,
    *,
    tz_name: str = "Europe/Moscow",
) -> dict[str, Any]:
    """Разрез конверсии по kind (work/home) и daypart (утро/день/вечер)."""

    tz = ZoneInfo(tz_name)
    steps = ["demo_sent", "demo_ack", "view_tariffs", "sub_paid"]
    source_events = tuple(
        dict.fromkeys(
            COMMERCIAL_STEP_EVENT_NAMES["demo"]
            + COMMERCIAL_STEP_EVENT_NAMES["listened"]
            + COMMERCIAL_STEP_EVENT_NAMES["offer"]
            + COMMERCIAL_STEP_EVENT_NAMES["paid_event"]
        )
    )

    per_user: dict[int, dict[str, Any]] = {}
    rows = _commercial_event_rows(list(source_events), start_utc, end_utc)
    for row in rows:
        uid = int(_row_value(row, "user_id", 0) or 0)
        name = str(_row_value(row, "name", 1) or "").strip()
        canonical_step = _breakdown_step(name)
        if not canonical_step:
            continue
        meta_raw = _row_value(row, "meta", 2) or "{}"
        ts_raw = str(_row_value(row, "created_at", 3) or "")
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
        except json.JSONDecodeError:
            meta = {}

        user = per_user.setdefault(uid, {"seen": set(), "touch": None})
        user["seen"].add(canonical_step)

        if canonical_step in ("demo_sent", "demo_ack"):
            kind = str(meta.get("kind") or "").strip().lower() or "unknown"
            if kind not in ("work", "home"):
                kind = "unknown"
            try:
                normalized_ts = ts_raw[:-1] + "+00:00" if ts_raw.endswith("Z") else ts_raw
                dt = datetime.fromisoformat(normalized_ts)
            except ValueError:
                dt = None
            if user["touch"] is None or (
                canonical_step == "demo_ack" and user["touch"].get("source") != "demo_ack"
            ):
                hour = None
                if dt is not None:
                    try:
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                        hour = dt.astimezone(tz).hour
                    except (ZoneInfoNotFoundError, ValueError):
                        hour = None
                user["touch"] = {
                    "kind": kind,
                    "hour": hour,
                    "daypart": _daypart_ru(hour if hour is not None else 12),
                    "source": canonical_step,
                }

    by_kind: dict[str, dict[str, int]] = {
        key: {step: 0 for step in steps} for key in ("work", "home", "unknown")
    }
    by_daypart: dict[str, dict[str, int]] = {
        key: {step: 0 for step in steps} for key in ("утро", "день", "вечер")
    }

    for user in per_user.values():
        touch = user.get("touch") or {"kind": "unknown", "daypart": "день"}
        kind = touch.get("kind") or "unknown"
        daypart = touch.get("daypart") or "день"
        seen = user.get("seen") or set()
        for step in steps:
            if step in seen:
                by_kind.setdefault(kind, {key: 0 for key in steps})[step] += 1
                by_daypart.setdefault(daypart, {key: 0 for key in steps})[step] += 1

    return {
        "steps": steps,
        "by_kind": by_kind,
        "by_daypart": by_daypart,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "tz": tz_name,
    }


__all__ = [
    "DEFAULT_STEPS",
    "conversion_breakdown",
    "conversion_report",
    "format_conversion_report",
]
