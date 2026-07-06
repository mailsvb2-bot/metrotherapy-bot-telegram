from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from services.db import db

_PERIOD_DAYS: dict[str, int | None] = {"today": 0, "week": 7, "month": 30, "all": None}


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


def _amount_rub(amount_minor: Any, currency: Any = "RUB") -> str:
    return f"{_safe_int(amount_minor) / 100:.0f} {currency or 'RUB'}"


def _where_period(period: str, alias: str = "p") -> tuple[str, list[Any]]:
    start = _period_start(period)
    if start:
        return f"WHERE COALESCE({alias}.created_at, '') >= ?", [start]
    return "", []


def ad_spend_summary(*, limit: int = 20) -> dict[str, Any]:
    """Return manual acquisition spend recorded through admin ad links.

    This intentionally reuses admin_ad_links.ad_spend instead of creating a second
    shadow source for ad budgets. Values are plain strings because the admin can
    enter human labels like "340rub" or "340 RUB"; numeric normalization can be
    added later behind a migration when we need strict accounting.
    """
    with db() as conn:
        try:
            rows = _rows(conn.execute(
                """
                SELECT source, campaign, creative, ad_spend, start_payload, url, created_at
                FROM admin_ad_links
                ORDER BY id DESC
                LIMIT ?
                """.strip(),
                (int(limit),),
            ).fetchall())
        except sqlite3.Error:
            rows = []
    filled = [r for r in rows if str(r.get("ad_spend") or "").strip()]
    missing = [r for r in rows if not str(r.get("ad_spend") or "").strip()]
    return {"ok": True, "rows": rows, "with_spend": len(filled), "without_spend": len(missing)}


def format_ad_spend_summary(report: dict[str, Any]) -> str:
    rows = report.get("rows") or []
    lines = [
        "📣 Расходы на рекламу",
        "",
        f"Ссылок с указанным расходом: {report.get('with_spend', 0)}",
        f"Ссылок без расхода: {report.get('without_spend', 0)}",
        "",
    ]
    if not rows:
        lines.append("Пока рекламных ссылок нет. Создайте ссылку в разделе «Рекламные ссылки»." )
        return "\n".join(lines)
    lines.append("Последние кампании:")
    for row in rows[:10]:
        lines.append(
            f"• {row.get('source')} / {row.get('campaign')} / {row.get('creative')} — расход: {row.get('ad_spend') or 'не указан'}"
        )
    lines.append("")
    lines.append("Чтобы расход попал в путь до оплаты, создавайте рекламную ссылку с расходом в названии/параметре." )
    return "\n".join(lines)


def access_alerts(*, limit: int = 20) -> list[dict[str, Any]]:
    """Payments that look paid but have no active access row.

    This is intentionally conservative: it flags succeeded/paid/captured payments
    where there is no active subscription for the same user.
    """
    with db() as conn:
        try:
            rows = _rows(conn.execute(
                """
                SELECT p.id, p.user_id, p.amount, p.currency, p.created_at, p.provider_status,
                       u.username, u.first_name,
                       s.status AS subscription_status, s.scope, s.plan_type
                FROM payments p
                LEFT JOIN users u ON u.user_id = p.user_id
                LEFT JOIN subscriptions s ON s.user_id = p.user_id AND COALESCE(s.status, '') = 'active'
                WHERE COALESCE(p.provider_status, 'succeeded') IN ('succeeded', 'paid', 'captured')
                  AND s.user_id IS NULL
                ORDER BY p.id DESC
                LIMIT ?
                """.strip(),
                (int(limit),),
            ).fetchall())
        except sqlite3.Error:
            rows = []
    return rows


def format_access_alerts(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "✅ Оплаты без выданного доступа сейчас не найдены."
    lines = [
        "🚨 Деньги есть — доступ не найден",
        "",
        "Проверьте эти оплаты вручную: платёж выглядит успешным, но активной подписки у пользователя не видно.",
        "",
    ]
    for row in rows[:20]:
        user = " ".join(x for x in [str(row.get("first_name") or "").strip(), ("@" + str(row.get("username"))) if row.get("username") else ""] if x) or f"user_id {row.get('user_id')}"
        lines.append(f"• Оплата #{row.get('id')} — {user} — {_amount_rub(row.get('amount'), row.get('currency'))} — {row.get('created_at') or '-'}")
    return "\n".join(lines)


def money_csv(period: str = "today", *, limit: int = 500) -> str:
    period = (period or "today").strip().lower()
    if period not in _PERIOD_DAYS:
        period = "today"
    start = _period_start(period)
    if start:
        sql = """
            SELECT p.id, p.user_id, p.amount, p.currency, p.created_at, p.provider_status,
                   u.username, u.first_name,
                   s.scope, s.plan_type, s.status AS subscription_status
            FROM payments p
            LEFT JOIN users u ON u.user_id = p.user_id
            LEFT JOIN subscriptions s ON s.user_id = p.user_id
            WHERE COALESCE(p.created_at, '') >= ?
            ORDER BY p.id DESC
            LIMIT ?
        """.strip()
        params = (start, int(limit))
    else:
        sql = """
            SELECT p.id, p.user_id, p.amount, p.currency, p.created_at, p.provider_status,
                   u.username, u.first_name,
                   s.scope, s.plan_type, s.status AS subscription_status
            FROM payments p
            LEFT JOIN users u ON u.user_id = p.user_id
            LEFT JOIN subscriptions s ON s.user_id = p.user_id
            ORDER BY p.id DESC
            LIMIT ?
        """.strip()
        params = (int(limit),)
    with db() as conn:
        rows = _rows(conn.execute(sql, params).fetchall())
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=[
        "payment_id", "user_id", "client", "amount", "currency", "paid_at", "payment_status", "subscription_status", "scope", "plan_type",
    ])
    writer.writeheader()
    for row in rows:
        client = " ".join(x for x in [str(row.get("first_name") or "").strip(), ("@" + str(row.get("username"))) if row.get("username") else ""] if x)
        writer.writerow({
            "payment_id": row.get("id"),
            "user_id": row.get("user_id"),
            "client": client,
            "amount": _safe_int(row.get("amount")) / 100,
            "currency": row.get("currency") or "RUB",
            "paid_at": row.get("created_at") or "",
            "payment_status": row.get("provider_status") or "",
            "subscription_status": row.get("subscription_status") or "",
            "scope": row.get("scope") or "",
            "plan_type": row.get("plan_type") or "",
        })
    return out.getvalue()


def full_growth_summary(period: str = "today") -> dict[str, Any]:
    return {
        "ok": True,
        "period": period,
        "ad_spend": ad_spend_summary(),
        "access_alerts": access_alerts(),
        "csv": money_csv(period),
    }
