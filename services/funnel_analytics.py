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


def _counts(names: list[str], start_utc: str | None = None, end_utc: str | None = None) -> dict[str, int]:
    if not names:
        return {}

    res: dict[str, int] = {n: 0 for n in names}

    where = f"name IN ({','.join('?' for _ in names)})"
    params: list[Any] = list(names)

    if start_utc:
        where += " AND created_at >= ?"
        params.append(start_utc)
    if end_utc:
        where += " AND created_at < ?"
        params.append(end_utc)

    q = (
        "SELECT name, COUNT(DISTINCT user_id) AS cnt "
        "FROM events "
        f"WHERE {where} "
        "GROUP BY name"
    )

    with db() as c:
        for row in c.execute(q, tuple(params)).fetchall():
            try:
                n = row[0]
                cnt = int(row[1] or 0)
            except (IndexError, TypeError, ValueError):
                logging.getLogger(__name__).exception("Bad row in funnel counts")
                continue
            if n in res:
                res[n] = cnt
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
    from datetime import datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)

    steps = ["demo_sent", "demo_ack", "view_tariffs", "sub_paid"]

    where_parts = [f"name IN ({','.join('?' for _ in steps)})"]
    params: list[Any] = list(steps)
    if start_utc:
        where_parts.append("created_at >= ?")
        params.append(start_utc)
    if end_utc:
        where_parts.append("created_at < ?")
        params.append(end_utc)
    where = " AND ".join(where_parts)

    q = (
        "SELECT user_id, name, meta, created_at "
        "FROM events "
        f"WHERE {where} "
        "ORDER BY created_at ASC"
    )

    # События по пользователям
    per_user: dict[int, dict[str, Any]] = {}
    with db() as c:
        rows = c.execute(q, tuple(params)).fetchall()
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