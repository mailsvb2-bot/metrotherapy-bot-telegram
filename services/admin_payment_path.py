from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from services.db import db

_PERIOD_DAYS: dict[str, int | None] = {"today": 0, "week": 7, "month": 30, "all": None}
_STEP_NAMES: dict[str, tuple[str, ...]] = {
    "start": ("start", "bot_start", "user_start"),
    "demo": ("funnel_demo_open", "funnel_demo_work", "funnel_demo_home", "demo_sent"),
    "listened": ("funnel_demo_ack", "audio_listened"),
    "offer": ("funnel_offer_shown", "view_tariffs", "sub_menu"),
    "pay_click": ("funnel_offer_pay_clicked", "pay_selected", "payment_started"),
    "paid": ("funnel_pay_success", "payment_success", "successful_payment"),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _period_start(period: str) -> str | None:
    period = (period or "today").strip().lower()
    if period == "today":
        now = _utc_now()
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
    days = _PERIOD_DAYS.get(period)
    if days is None:
        return None
    return (_utc_now() - timedelta(days=int(days))).isoformat()


def _rowdict(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except TypeError:
        return None
    except ValueError:
        return None


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = _rowdict(row)
        if item is not None:
            out.append(item)
    return out


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except TypeError:
        return 0
    except ValueError:
        return 0


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _short_dt(value: Any) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%d.%m %H:%M") if dt else "—"


def _human_delta(start: Any, end: Any) -> str:
    a = _parse_dt(start)
    b = _parse_dt(end)
    if not a or not b:
        return "не посчитано"
    seconds = max(int((b - a).total_seconds()), 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days} д. {hours} ч."
    if hours:
        return f"{hours} ч. {minutes} мин."
    return f"{minutes} мин."


def _amount_rub(amount_minor: Any, currency: Any = "RUB") -> str:
    amount = _safe_int(amount_minor) / 100
    return f"{amount:.0f} {currency or 'RUB'}"


def _meta_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _event_time(events: list[dict[str, Any]], names: tuple[str, ...]) -> str:
    allowed = set(names)
    for event in events:
        name = str(event.get("name") or event.get("event") or "").strip()
        if name in allowed:
            return str(event.get("created_at") or "")
    return ""


def _attribution(events: list[dict[str, Any]]) -> dict[str, str]:
    for event in events:
        meta = _meta_dict(event.get("meta") or "{}")
        if any(k in meta for k in ("utm_source", "source", "utm_campaign", "campaign", "utm_creative", "creative", "utm_content", "ad_spend", "cost")):
            return {
                "source": str(meta.get("utm_source") or meta.get("source") or "не подключено").strip() or "не подключено",
                "campaign": str(meta.get("utm_campaign") or meta.get("campaign") or "не подключено").strip() or "не подключено",
                "creative": str(meta.get("utm_creative") or meta.get("creative") or meta.get("utm_content") or "не подключено").strip() or "не подключено",
                "ad_spend": str(meta.get("ad_spend") or meta.get("cost") or "не подключено").strip() or "не подключено",
            }
    return {"source": "не подключено", "campaign": "не подключено", "creative": "не подключено", "ad_spend": "не подключено"}


def _payment_where(period: str) -> tuple[str, list[Any]]:
    start = _period_start(period)
    if start:
        return "WHERE COALESCE(p.created_at, '') >= ?", [start]
    return "", []


def _path_row(payment: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    paid_at = str(payment.get("created_at") or payment.get("paid_at") or "")
    start_at = _event_time(events, _STEP_NAMES["start"]) or str(payment.get("joined_at") or "")
    demo_at = _event_time(events, _STEP_NAMES["demo"])
    listened_at = _event_time(events, _STEP_NAMES["listened"])
    offer_at = _event_time(events, _STEP_NAMES["offer"])
    pay_click_at = _event_time(events, _STEP_NAMES["pay_click"])
    paid_event_at = _event_time(events, _STEP_NAMES["paid"]) or paid_at
    username = f"@{payment.get('username')}" if payment.get("username") else ""
    name = " ".join(x for x in [str(payment.get("first_name") or "").strip(), username] if x).strip()
    return {
        "payment_id": _safe_int(payment.get("id")),
        "user_id": _safe_int(payment.get("user_id")),
        "client": name or f"user_id {payment.get('user_id')}",
        "amount": _amount_rub(payment.get("amount"), payment.get("currency")),
        "status": payment.get("provider_status") or "получена",
        "attribution": _attribution(events),
        "start_at": start_at,
        "demo_at": demo_at,
        "listened_at": listened_at,
        "offer_at": offer_at,
        "pay_click_at": pay_click_at,
        "paid_at": paid_event_at,
        "time_to_payment": _human_delta(start_at, paid_event_at),
    }


def payment_path_report(period: str = "today", *, limit: int = 10) -> dict[str, Any]:
    period = (period or "today").strip().lower()
    if period not in _PERIOD_DAYS:
        period = "today"
    start = _period_start(period)

    if start:
        total_sql = (
            "SELECT COUNT(1) AS n, COALESCE(SUM(amount), 0) AS amount "
            "FROM payments p WHERE COALESCE(p.created_at, '') >= ?"
        )
        payments_sql = """
            SELECT p.id, p.user_id, p.amount, p.currency, p.created_at, p.provider_status,
                   u.username, u.first_name, u.joined_at
            FROM payments p
            LEFT JOIN users u ON u.user_id = p.user_id
            WHERE COALESCE(p.created_at, '') >= ?
            ORDER BY p.id DESC
            LIMIT ?
        """.strip()
        total_params: tuple[Any, ...] = (start,)
        payments_params: tuple[Any, ...] = (start, int(limit))
    else:
        total_sql = "SELECT COUNT(1) AS n, COALESCE(SUM(amount), 0) AS amount FROM payments p"
        payments_sql = """
            SELECT p.id, p.user_id, p.amount, p.currency, p.created_at, p.provider_status,
                   u.username, u.first_name, u.joined_at
            FROM payments p
            LEFT JOIN users u ON u.user_id = p.user_id
            ORDER BY p.id DESC
            LIMIT ?
        """.strip()
        total_params = ()
        payments_params = (int(limit),)

    with db() as conn:
        total = _rowdict(conn.execute(total_sql, total_params).fetchone()) or {}
        payments = _rows(conn.execute(payments_sql, payments_params).fetchall())
        rows: list[dict[str, Any]] = []
        for payment in payments:
            user_id = _safe_int(payment.get("user_id"))
            paid_at = str(payment.get("created_at") or "")
            events = _rows(conn.execute(
                """
                SELECT name, meta, created_at
                FROM events
                WHERE user_id=? AND (COALESCE(created_at, '') <= ? OR ? = '')
                ORDER BY COALESCE(created_at, '') ASC, id ASC
                LIMIT 80
                """.strip(),
                (user_id, paid_at, paid_at),
            ).fetchall()) if user_id else []
            rows.append(_path_row(payment, events))
    return {"period": period, "count": _safe_int(total.get("n")), "amount": _safe_int(total.get("amount")), "rows": rows}


def format_payment_path_report(report: dict[str, Any]) -> str:
    labels = {"today": "сегодня", "week": "за 7 дней", "month": "за 30 дней", "all": "за всё время"}
    period = str(report.get("period") or "today")
    rows = report.get("rows") or []
    lines = [
        f"📉 Путь до оплаты — {labels.get(period, period)}",
        "",
        f"Оплат: {report.get('count', 0)}",
        f"Сумма: {_amount_rub(report.get('amount'), 'RUB')}",
        "",
    ]
    if not rows:
        lines.append("Оплат за этот период пока нет. Когда появятся оплаты, здесь будет путь каждого клиента: источник → демо → предложение → клик оплаты → оплата.")
        return "\n".join(lines)
    lines.append("Последние клиенты, которые дошли до оплаты:")
    for row in rows[:10]:
        attr = row.get("attribution") or {}
        path = " → ".join([
            f"/start {_short_dt(row.get('start_at'))}",
            f"демо {_short_dt(row.get('demo_at'))}",
            f"прослушал {_short_dt(row.get('listened_at'))}",
            f"предложение {_short_dt(row.get('offer_at'))}",
            f"клик оплаты {_short_dt(row.get('pay_click_at'))}",
            f"оплата {_short_dt(row.get('paid_at'))}",
        ])
        lines.extend([
            "",
            f"#{row.get('payment_id')} — {row.get('client')} — {row.get('amount')} — {row.get('status')}",
            f"Откуда: {attr.get('source')} / {attr.get('campaign')} / {attr.get('creative')}",
            f"Расход на привлечение: {attr.get('ad_spend')}",
            f"От /start до оплаты: {row.get('time_to_payment')}",
            f"Путь: {path}",
        ])
    lines.extend(["", "Нажмите на оплату ниже, чтобы открыть карточку клиента: креатив, расход, сумма, прослушивания, подарки, рекомендации и повторная покупка."])
    return "\n".join(lines)
