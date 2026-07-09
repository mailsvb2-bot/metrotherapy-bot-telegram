from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from services.db import db
from services.growth_autopilot_core import (
    SAFE_AUTOPILOT_MODE,
    data_confidence,
    data_gaps,
    diagnose_growth_snapshot,
    format_growth_autopilot_report,
    parse_ad_spend_to_minor,
    pct,
    safe_int,
)

log = logging.getLogger(__name__)

_PERIOD_DAYS: dict[str, int | None] = {
    "today": 0,
    "week": 7,
    "month": 30,
    "all": None,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _period_start(period: str) -> str | None:
    normalized = normalize_period(period)
    if normalized == "today":
        now = _utc_now()
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
    days = _PERIOD_DAYS.get(normalized)
    if days is None:
        return None
    return (_utc_now() - timedelta(days=int(days))).isoformat()


def normalize_period(period: str | None) -> str:
    value = (period or "today").strip().lower()
    return value if value in _PERIOD_DAYS else "today"


def _rowdict(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return None


def _rows(items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in items or []:
        item = _rowdict(row)
        if item is not None:
            out.append(item)
    return out


def _table_columns(conn: Any, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except (sqlite3.Error, OSError, TypeError):
        log.debug("table info read failed for %s", table, exc_info=True)
        return set()


def _fetch_rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        with db() as conn:
            return _rows(conn.execute(sql, params).fetchall())
    except (sqlite3.Error, OSError, TypeError):
        log.debug("growth autopilot query skipped", exc_info=True)
        return []


def _fetch_scalar(sql: str, params: tuple[Any, ...] = ()) -> int:
    try:
        with db() as conn:
            row = conn.execute(sql, params).fetchone()
        if row is None:
            return 0
        if hasattr(row, "keys"):
            keys = list(row.keys())
            return safe_int(row[keys[0]]) if keys else 0
        return safe_int(row[0])
    except (sqlite3.Error, OSError, TypeError):
        log.debug("growth autopilot scalar query skipped", exc_info=True)
        return 0


def _event_counts(period: str) -> dict[str, int]:
    start = _period_start(period)
    names = [
        "funnel_start_command",
        "demo_sent",
        "demo_ack",
        "sub_menu_open",
        "funnel_tariffs_command",
        "payment_started",
        "payment_success",
        "gift_paid",
        "gift_redeemed",
    ]
    out = {name: 0 for name in names}
    where = "WHERE name=? AND COALESCE(created_at, '') >= ?" if start else "WHERE name=?"
    for name in names:
        params: tuple[Any, ...] = (name, start) if start else (name,)
        out[name] = _fetch_scalar(f"SELECT COUNT(DISTINCT user_id) AS c FROM events {where}", params)
    return out


def _demo_counts(period: str) -> dict[str, int]:
    start = _period_start(period)
    params: tuple[Any, ...] = (start,) if start else ()
    tail = "WHERE COALESCE(sent_at_utc, '') >= ?" if start else ""
    ack_tail = "WHERE ack_at_utc IS NOT NULL AND COALESCE(sent_at_utc, '') >= ?" if start else "WHERE ack_at_utc IS NOT NULL"
    return {
        "sent_users": _fetch_scalar(f"SELECT COUNT(DISTINCT user_id) AS c FROM demo_events {tail}", params),
        "sent_total": _fetch_scalar(f"SELECT COUNT(*) AS c FROM demo_events {tail}", params),
        "ack_users": _fetch_scalar(f"SELECT COUNT(DISTINCT user_id) AS c FROM demo_events {ack_tail}", params),
        "ack_total": _fetch_scalar(f"SELECT COUNT(*) AS c FROM demo_events {ack_tail}", params),
    }


def _payment_summary(period: str) -> dict[str, Any]:
    start = _period_start(period)
    try:
        with db() as conn:
            cols = _table_columns(conn, "payments")
            if not cols:
                return {
                    "payments": 0,
                    "paid_users": 0,
                    "revenue_minor": 0,
                    "currency": "RUB",
                    "status_source": "missing_table",
                }

            status_col = "provider_status" if "provider_status" in cols else ("status" if "status" in cols else "")
            amount_col = "amount" if "amount" in cols else ("amount_minor" if "amount_minor" in cols else "")
            created_col = "created_at" if "created_at" in cols else ("paid_at" if "paid_at" in cols else "")
            currency_col = "currency" if "currency" in cols else ""
            user_expr = "user_id" if "user_id" in cols else "NULL"

            conditions: list[str] = []
            params: list[Any] = []
            if status_col:
                conditions.append(f"COALESCE({status_col}, 'succeeded') IN ('succeeded','paid','success','captured')")
            if start and created_col:
                conditions.append(f"COALESCE({created_col}, '') >= ?")
                params.append(start)
            where = "WHERE " + " AND ".join(conditions) if conditions else ""

            amount_expr = f"COALESCE({amount_col}, 0)" if amount_col else "0"
            currency_expr = f"COALESCE(MAX({currency_col}), 'RUB')" if currency_col else "'RUB'"
            row = conn.execute(
                "SELECT "
                "COUNT(*) AS payments, "
                f"COUNT(DISTINCT {user_expr}) AS paid_users, "
                f"SUM({amount_expr}) AS revenue_minor, "
                f"{currency_expr} AS currency "
                f"FROM payments {where}",
                tuple(params),
            ).fetchone()
            data = _rowdict(row) or {}
            return {
                "payments": safe_int(data.get("payments")),
                "paid_users": safe_int(data.get("paid_users")),
                "revenue_minor": safe_int(data.get("revenue_minor")),
                "currency": str(data.get("currency") or "RUB"),
                "status_source": status_col or "none",
            }
    except (sqlite3.Error, OSError, TypeError):
        log.debug("payment summary failed", exc_info=True)
        return {"payments": 0, "paid_users": 0, "revenue_minor": 0, "currency": "RUB", "status_source": "error"}


def _ad_link_summary(period: str, *, limit: int = 50) -> dict[str, Any]:
    start = _period_start(period)
    where = "WHERE COALESCE(created_at, '') >= ?" if start else ""
    params: tuple[Any, ...] = (start, int(limit)) if start else (int(limit),)
    rows = _fetch_rows(
        f"""
        SELECT source, campaign, creative, ad_spend, start_payload, url, created_at
        FROM admin_ad_links
        {where}
        ORDER BY id DESC
        LIMIT ?
        """.strip(),
        params,
    )
    spend_minor = 0
    with_spend = 0
    latest: list[dict[str, Any]] = []
    by_source: dict[str, dict[str, Any]] = {}
    for row in rows:
        parsed = parse_ad_spend_to_minor(row.get("ad_spend"))
        source = str(row.get("source") or "unknown")
        bucket = by_source.setdefault(source, {"source": source, "links": 0, "with_spend": 0, "spend_minor": 0})
        bucket["links"] += 1
        if parsed is not None:
            with_spend += 1
            spend_minor += parsed
            bucket["with_spend"] += 1
            bucket["spend_minor"] += parsed
        item = dict(row)
        item["parsed_spend_minor"] = parsed
        latest.append(item)
    return {
        "links": len(rows),
        "with_spend": with_spend,
        "without_spend": max(0, len(rows) - with_spend),
        "spend_minor_low_confidence": spend_minor,
        "by_source": sorted(by_source.values(), key=lambda x: str(x.get("source"))),
        "latest": latest[:10],
    }


def _safe_access_alerts(period: str, *, limit: int = 20) -> list[dict[str, Any]]:
    start = _period_start(period)
    period_clause = "AND COALESCE(p.created_at, '') >= ?" if start else ""
    params: tuple[Any, ...] = (start, int(limit)) if start else (int(limit),)
    return _fetch_rows(
        f"""
        SELECT p.id, p.user_id, p.amount, p.currency, p.created_at, p.provider_status,
               u.username, u.first_name,
               s.status AS subscription_status, s.scope, s.plan_type
        FROM payments p
        LEFT JOIN users u ON u.user_id = p.user_id
        LEFT JOIN subscriptions s ON s.user_id = p.user_id AND COALESCE(s.status, '') = 'active'
        WHERE COALESCE(p.provider_status, 'succeeded') IN ('succeeded', 'paid', 'captured')
          AND s.user_id IS NULL
          {period_clause}
        ORDER BY p.id DESC
        LIMIT ?
        """.strip(),
        params,
    )


def _safe_segments() -> dict[str, int]:
    try:
        from services.segments import segment_counts
    except ImportError:
        log.debug("segment counts import unavailable", exc_info=True)
        return {}
    try:
        return {str(k): safe_int(v) for k, v in (segment_counts(limit_users=5000) or {}).items()}
    except (sqlite3.Error, OSError, TypeError):
        log.debug("segment counts unavailable", exc_info=True)
        return {}


def _safe_funnel2() -> dict[str, Any]:
    try:
        from services.funnel2_analytics import scenario_counts
    except ImportError:
        log.debug("funnel2 counts import unavailable", exc_info=True)
        return {}
    try:
        return dict(scenario_counts() or {})
    except (sqlite3.Error, OSError, TypeError):
        log.debug("funnel2 counts unavailable", exc_info=True)
        return {}


def build_growth_autopilot_snapshot(period: str = "today") -> dict[str, Any]:
    """Build a read-only Growth Autopilot evidence snapshot.

    No writes, no external calls, no budget mutations.
    """

    period = normalize_period(period)
    events = _event_counts(period)
    demo = _demo_counts(period)
    payments = _payment_summary(period)
    ad_links = _ad_link_summary(period)
    access_alert_rows = _safe_access_alerts(period)
    segments = _safe_segments()
    funnel2 = _safe_funnel2()

    start_users = max(events.get("funnel_start_command", 0), 0)
    demo_sent = max(demo.get("sent_users", 0), events.get("demo_sent", 0))
    demo_ack = max(demo.get("ack_users", 0), events.get("demo_ack", 0))
    tariff_open = max(events.get("sub_menu_open", 0), events.get("funnel_tariffs_command", 0))
    pay_click = events.get("payment_started", 0)
    paid = safe_int(payments.get("paid_users"))

    funnel = {
        "start_users": start_users,
        "demo_sent_users": demo_sent,
        "demo_ack_users": demo_ack,
        "tariff_open_users": tariff_open,
        "payment_started_users": pay_click,
        "paid_users": paid,
        "start_to_demo_pct": pct(demo_sent, start_users),
        "demo_to_ack_pct": pct(demo_ack, demo_sent),
        "ack_to_tariff_pct": pct(tariff_open, demo_ack),
        "tariff_to_paid_pct": pct(paid, tariff_open),
        "start_to_paid_pct": pct(paid, start_users),
    }

    quality = {
        "mode": SAFE_AUTOPILOT_MODE,
        "external_writes_enabled": False,
        "budget_writes_enabled": False,
        "conversion_postbacks_enabled": False,
        "confidence": data_confidence(ad_links=ad_links, payments=payments, funnel=funnel),
        "gaps": data_gaps(ad_links=ad_links, payments=payments, funnel=funnel),
    }

    snapshot = {
        "ok": True,
        "period": period,
        "generated_at_utc": _utc_now().isoformat(),
        "autopilot_mode": SAFE_AUTOPILOT_MODE,
        "data_quality": quality,
        "ad_links": ad_links,
        "funnel": funnel,
        "payments": payments,
        "access_alerts": {"count": len(access_alert_rows), "rows": access_alert_rows[:5]},
        "segments": segments,
        "funnel2": funnel2,
    }
    snapshot["recommendations"] = diagnose_growth_snapshot(snapshot)
    return snapshot


def build_growth_autopilot_report(period: str = "today") -> str:
    return format_growth_autopilot_report(build_growth_autopilot_snapshot(period))


def build_growth_action_inbox_report(period: str = "today") -> str:
    from services.growth_action_inbox import format_action_inbox

    return format_action_inbox(build_growth_autopilot_snapshot(period))
