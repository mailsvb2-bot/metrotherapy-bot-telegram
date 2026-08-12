from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
    ("demo_sent", "Получили демо"),
    ("demo_ack", "Подтвердили/прослушали демо"),
    ("view_tariffs", "Открыли тарифы"),
    ("payment_started", "Создан checkout"),
    ("paid", "Оплатили пакет"),
)


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
    paid_users = _paid_user_count(start_utc, end_utc)

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
            ("demo_sent", int(counts.get("demo_sent", 0))),
            ("demo_ack", int(counts.get("demo_ack", 0))),
            ("view_tariffs", int(counts.get("view_tariffs", 0))),
            ("payment_started", int(counts.get("payment_started", 0))),
            ("paid", paid_users),
        ]
    )

    return {
        "counts": counts,
        "chain": legacy_chain,
        "money_chain": money_chain,
        "paid_users": paid_users,
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
                    "🎯 Самый слабый переход: "
                    f"{labels.get(step, step)} — {float(worst.get('from_prev_pct') or 0.0):.1f}%"
                ),
            ]
        )
    elif chain:
        lines.extend(["", "🎯 Явной точки потери в доступных данных пока нет."])

    lines.extend(
        [
            "",
            "Оплата считается по успешным payment_token_grants; события используются только для шагов до оплаты.",
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


def conversion_breakdown(
    start_utc: str | None = None,
    end_utc: str | None = None,
    *,
    tz_name: str = "Europe/Moscow",
) -> dict[str, Any]:
    """Разрез конверсии по kind (work/home) и daypart (утро/день/вечер)."""

    tz = ZoneInfo(tz_name)
    steps = ["demo_sent", "demo_ack", "view_tariffs", "sub_paid"]

    per_user: dict[int, dict[str, Any]] = {}
    rows = _commercial_event_rows(steps, start_utc, end_utc)
    for row in rows:
        uid = int(_row_value(row, "user_id", 0) or 0)
        name = str(_row_value(row, "name", 1) or "").strip()
        meta_raw = _row_value(row, "meta", 2) or "{}"
        ts_raw = str(_row_value(row, "created_at", 3) or "")
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
        except json.JSONDecodeError:
            meta = {}

        user = per_user.setdefault(uid, {"seen": set(), "touch": None})
        user["seen"].add(name)

        if name in ("demo_sent", "demo_ack"):
            kind = str(meta.get("kind") or "").strip().lower() or "unknown"
            if kind not in ("work", "home"):
                kind = "unknown"
            try:
                normalized_ts = ts_raw[:-1] + "+00:00" if ts_raw.endswith("Z") else ts_raw
                dt = datetime.fromisoformat(normalized_ts)
            except ValueError:
                dt = None
            if user["touch"] is None or (
                name == "demo_ack" and user["touch"].get("source") != "demo_ack"
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
                    "source": name,
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
