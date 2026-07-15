from __future__ import annotations
import logging
from zoneinfo import ZoneInfoNotFoundError


from datetime import datetime
from typing import Any

from services.db import db


DEFAULT_STEPS: list[str] = [
    # демо
    "demo_sent",
    "demo_ack",
    # автоворонка (факт отправки сообщений)
    "funnel_nudge_sent",
    "funnel_offer_sent",
    "funnel_deadline_sent",
    "funnel_lastcall_sent",
    # действия
    "view_tariffs",
    "invoice_created",
    "invoice_paid",
    "sub_paid",
]


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


def _counts(names: list[str], start_utc: str | None = None, end_utc: str | None = None) -> dict[str, int]:
    if not names:
        return {}

    res: dict[str, int] = {n: 0 for n in names}
    sql, mode = _event_count_sql(start_utc, end_utc)

    with db() as c:
        for name in names:
            row = c.execute(sql, _date_params(mode, name, start_utc, end_utc)).fetchone()
            try:
                res[name] = int((row[0] if row else 0) or 0)
            except (IndexError, TypeError, ValueError):
                logging.getLogger(__name__).exception("Bad row in funnel counts")
                res[name] = 0
    return res


def conversion_report(start_utc: str | None = None, end_utc: str | None = None, *, steps: list[str] | None = None) -> dict[str, Any]:
    """Отчёт по конверсии: уникальные пользователи на шагах + проценты."""

    steps = steps or DEFAULT_STEPS
    c = _counts(steps, start_utc, end_utc)

    # базовая цепочка конверсии (можно расширять)
    chain = [
        "demo_sent",
        "demo_ack",
        "view_tariffs",
        "sub_paid",
    ]

    rates: list[dict[str, Any]] = []
    prev = None
    for name in chain:
        v = int(c.get(name, 0))
        if prev is None:
            rates.append({"step": name, "users": v, "from_prev_pct": None})
        else:
            pct = (v / prev * 100.0) if prev > 0 else 0.0
            rates.append({"step": name, "users": v, "from_prev_pct": round(pct, 1)})
        prev = v

    return {"counts": c, "chain": rates, "start_utc": start_utc, "end_utc": end_utc}


def _daypart_ru(hour: int) -> str:
    """Утро/день/вечер для аналитики.

    По ТЗ: утро/день/вечер. Ночь включаем в "вечер", чтобы не дробить отчёт.
    """
    h = int(hour) % 24
    if 5 <= h <= 11:
        return "утро"
    if 12 <= h <= 16:
        return "день"
    return "вечер"


def conversion_breakdown(start_utc: str | None = None, end_utc: str | None = None, *, tz_name: str = "Europe/Moscow") -> dict[str, Any]:
    """Разрез конверсии по kind (work/home) и daypart (утро/день/вечер).

    Принцип: атрибутируем пользователя по его "первому касанию" в диапазоне:
      - если есть demo_ack в диапазоне: берём kind и время demo_ack
      - иначе если есть demo_sent: берём kind и время demo_sent
    Дальше считаем, сколько таких пользователей достигли каждого шага.
    """
    import json
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)

    steps = ["demo_sent", "demo_ack", "view_tariffs", "sub_paid"]

    sql, mode = _event_rows_sql(start_utc, end_utc)

    # События по пользователям
    per_user: dict[int, dict[str, Any]] = {}
    with db() as c:
        rows = []
        for step in steps:
            rows.extend(c.execute(sql, _date_params(mode, step, start_utc, end_utc)).fetchall())
        rows.sort(key=lambda row: str(row[3] or ""))
        for r in rows:
            uid = int(r[0])
            name = (r[1] or "").strip()
            meta_raw = r[2] or "{}"
            ts_raw = r[3] or ""
            try:
                meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
            except json.JSONDecodeError:
                meta = {}

            u = per_user.setdefault(uid, {"seen": set(), "touch": None})
            u["seen"].add(name)

            # Первое касание: demo_ack предпочитаем demo_sent
            if name in ("demo_sent", "demo_ack"):
                kind = (meta.get("kind") or "").strip().lower() or "unknown"
                if kind not in ("work", "home"):
                    kind = "unknown"
                try:
                    dt = datetime.fromisoformat(ts_raw)
                except ValueError:
                    dt = None
                # приоритет demo_ack
                if u["touch"] is None or (name == "demo_ack" and u["touch"].get("source") != "demo_ack"):
                    hour = None
                    if dt is not None:
                        try:
                            hour = dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).hour
                        except (ZoneInfoNotFoundError, ValueError):
                            hour = None
                    u["touch"] = {
                        "kind": kind,
                        "hour": hour,
                        "daypart": _daypart_ru(hour if hour is not None else 12),
                        "source": name,
                    }

    # Счётчики
    by_kind: dict[str, dict[str, int]] = {k: {s: 0 for s in steps} for k in ("work", "home", "unknown")}
    by_daypart: dict[str, dict[str, int]] = {k: {s: 0 for s in steps} for k in ("утро", "день", "вечер")}

    for uid, u in per_user.items():
        touch = u.get("touch") or {"kind": "unknown", "daypart": "день"}
        kind = touch.get("kind") or "unknown"
        dp = touch.get("daypart") or "день"
        seen = u.get("seen") or set()
        for s in steps:
            if s in seen:
                by_kind.setdefault(kind, {k: 0 for k in steps})[s] += 1
                by_daypart.setdefault(dp, {k: 0 for k in steps})[s] += 1

    return {
        "steps": steps,
        "by_kind": by_kind,
        "by_daypart": by_daypart,
        "start_utc": start_utc,
        "end_utc": end_utc,
        "tz": tz_name,
    }
