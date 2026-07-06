from __future__ import annotations
import logging


from typing import Any

from services.db import db


STEPS = [
    # share
    "share_menu",
    "share_pick",
    "share_sent_ok",
    "share_sent_fail",
    # gift
    "gift_menu",
    "gift_target_picked",
    "gift_invoice_created",
    "gift_paid",
    "gift_delivered_ok",
    "gift_delivered_fail",
    "gift_redeemed",
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
                logging.getLogger(__name__).exception("Bad row in gift-share counts")
                res[name] = 0
    return res


def report(start_utc: str | None = None, end_utc: str | None = None) -> dict[str, Any]:
    c = _counts(STEPS, start_utc, end_utc)
    # Две цепочки
    share_chain = ["share_menu", "share_pick", "share_sent_ok"]
    gift_chain = ["gift_menu", "gift_target_picked", "gift_invoice_created", "gift_paid", "gift_redeemed"]

    def _chain(chain: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        prev = None
        for s in chain:
            v = int(c.get(s, 0))
            if prev is None:
                out.append({"step": s, "users": v, "from_prev_pct": None})
            else:
                pct = (v / prev * 100.0) if prev > 0 else 0.0
                out.append({"step": s, "users": v, "from_prev_pct": round(pct, 1)})
            prev = v
        return out

    return {
        "counts": c,
        "share_chain": _chain(share_chain),
        "gift_chain": _chain(gift_chain),
        "start_utc": start_utc,
        "end_utc": end_utc,
    }
