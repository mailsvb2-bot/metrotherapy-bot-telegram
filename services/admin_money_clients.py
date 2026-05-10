from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from services.admin_cards import user_card
from services.db import db


_PERIOD_DAYS: dict[str, int | None] = {
    "today": 0,
    "week": 7,
    "month": 30,
    "all": None,
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
    except (TypeError, ValueError):
        return None


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows or []:
        item = _rowdict(row)
        if item is not None:
            out.append(item)
    return out


def _amount_rub(amount_minor: Any, currency: Any = "RUB") -> str:
    try:
        amount = int(amount_minor or 0) / 100
    except (TypeError, ValueError):
        amount = 0
    return f"{amount:.0f} {currency or 'RUB'}"


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


def _safe_count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        if not row:
            return 0
        if isinstance(row, dict):
            return int(row.get("n") or 0)
        return int(row[0] or 0)
    except (sqlite3.Error, KeyError, TypeError, ValueError):
        return 0


def _safe_rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    try:
        return _rows(conn.execute(sql, params).fetchall())
    except sqlite3.Error:
        return []


def _payment_where(period: str) -> tuple[str, list[Any]]:
    start = _period_start(period)
    if start:
        return "WHERE COALESCE(p.created_at, '') >= ?", [start]
    return "", []


def money_period_summary(period: str = "today", *, limit: int = 20) -> dict[str, Any]:
    """Payment list for the admin money cockpit.

    This is intentionally factual: it uses only stored payment/subscription/user
    data. Advertising spend and creative attribution stay explicit as
    "not connected" until a real attribution table is added.
    """
    period = (period or "today").strip().lower()
    if period not in _PERIOD_DAYS:
        period = "today"
    where, params = _payment_where(period)
    with db() as conn:
        total = conn.execute(
            f"SELECT COUNT(1) AS n, COALESCE(SUM(amount), 0) AS amount FROM payments p {where}",
            tuple(params),
        ).fetchone()
        problems = conn.execute(
            f"""
            SELECT COUNT(1) AS n
            FROM payments p
            {where + (' AND' if where else 'WHERE')} (
                COALESCE(p.problem, '') <> ''
                OR p.provider_status IN ('canceled', 'waiting_for_capture')
            )
            """.strip(),
            tuple(params),
        ).fetchone()
        paid_users = conn.execute(
            f"SELECT COUNT(DISTINCT user_id) AS n FROM payments p {where}",
            tuple(params),
        ).fetchone()
        payment_rows = conn.execute(
            f"""
            SELECT
                p.id, p.user_id, p.amount, p.currency, p.created_at,
                p.provider_status, p.problem, p.payload,
                u.username, u.first_name,
                s.scope, s.plan_type, s.used_morning, s.used_evening,
                s.total_morning, s.total_evening, s.status AS subscription_status
            FROM payments p
            LEFT JOIN users u ON u.user_id = p.user_id
            LEFT JOIN subscriptions s ON s.user_id = p.user_id
            {where}
            ORDER BY p.id DESC
            LIMIT ?
            """.strip(),
            tuple(params + [int(limit)]),
        ).fetchall()

    total_d = _rowdict(total) or {}
    problems_d = _rowdict(problems) or {}
    paid_users_d = _rowdict(paid_users) or {}
    return {
        "period": period,
        "count": int(total_d.get("n") or 0),
        "amount": int(total_d.get("amount") or 0),
        "paid_users": int(paid_users_d.get("n") or 0),
        "problems": int(problems_d.get("n") or 0),
        "rows": _rows(payment_rows),
    }


def _find_first_start_event(conn: Any, user_id: int) -> dict[str, Any] | None:
    rows = _safe_rows(
        conn,
        """
        SELECT name, meta, created_at
        FROM events
        WHERE user_id=?
        ORDER BY COALESCE(created_at, ts, '') ASC, id ASC
        LIMIT 20
        """.strip(),
        (int(user_id),),
    )
    for row in rows:
        name = str(row.get("name") or row.get("event") or "").strip()
        meta_raw = row.get("meta") or "{}"
        try:
            meta = json.loads(meta_raw) if isinstance(meta_raw, str) else {}
        except json.JSONDecodeError:
            meta = {}
        if name in {"start", "bot_start", "user_start", "demo_sent", "view_tariffs"} or any(k in meta for k in ("utm_source", "utm_campaign", "utm_creative", "creative")):
            return {"name": name or "первое событие", "meta": meta, "created_at": row.get("created_at")}
    return rows[0] if rows else None


def _attribution_from_event(event: dict[str, Any] | None) -> dict[str, str]:
    meta = event.get("meta") if event else {}
    if not isinstance(meta, dict):
        meta = {}
    return {
        "source": str(meta.get("utm_source") or meta.get("source") or "не подключено").strip() or "не подключено",
        "campaign": str(meta.get("utm_campaign") or meta.get("campaign") or "не подключено").strip() or "не подключено",
        "creative": str(meta.get("utm_creative") or meta.get("creative") or meta.get("utm_content") or "не подключено").strip() or "не подключено",
        "ad_spend": str(meta.get("ad_spend") or meta.get("cost") or "не подключено").strip() or "не подключено",
    }


def _repeat_purchase_score(*, sub: dict[str, Any], invited_count: int, gift_created: int, timeline_count: int) -> dict[str, str]:
    total = int(sub.get("total_morning") or 0) + int(sub.get("total_evening") or 0)
    used = int(sub.get("used_morning") or 0) + int(sub.get("used_evening") or 0)
    ratio = (used / total) if total > 0 else 0.0
    points = 0
    reasons: list[str] = []
    if str(sub.get("status") or "").lower() == "active":
        points += 1
        reasons.append("подписка активна")
    if ratio >= 0.5:
        points += 1
        reasons.append("пользуется купленным доступом")
    if timeline_count >= 3:
        points += 1
        reasons.append("есть повторные прослушивания")
    if invited_count or gift_created:
        points += 1
        reasons.append("есть социальное действие: подарок или приглашение")

    if points >= 3:
        label = "высокая"
        action = "За 1–2 дня до окончания доступа мягко предложить продление и подарок для близкого."
    elif points >= 1:
        label = "средняя"
        action = "Показать пользу уже пройденных сессий и предложить следующий короткий маршрут."
    else:
        label = "низкая"
        action = "Сначала вернуть в прослушивание: напомнить о бесплатной/оставшейся практике без давления."
    return {"label": label, "why": ", ".join(reasons) if reasons else "мало действий после входа", "action": action}


def payment_client_card(payment_id: int) -> dict[str, Any]:
    payment_id = int(payment_id)
    with db() as conn:
        payment = _rowdict(conn.execute(
            """
            SELECT p.*, u.username, u.first_name, u.joined_at,
                   s.scope, s.plan_type, s.total_morning, s.total_evening,
                   s.used_morning, s.used_evening, s.status AS subscription_status,
                   s.started_at, s.paid_at
            FROM payments p
            LEFT JOIN users u ON u.user_id = p.user_id
            LEFT JOIN subscriptions s ON s.user_id = p.user_id
            WHERE p.id=?
            LIMIT 1
            """.strip(),
            (payment_id,),
        ).fetchone())
        if not payment:
            return {"ok": False, "payment_id": payment_id, "reason": "payment_not_found"}

        user_id = int(payment.get("user_id") or 0)
        first_event = _find_first_start_event(conn, user_id) if user_id else None
        gift_created = _safe_count(conn, "SELECT COUNT(1) AS n FROM gift_codes WHERE created_by=?", (user_id,))
        gift_redeemed = _safe_count(conn, "SELECT COUNT(1) AS n FROM gift_codes WHERE created_by=? AND COALESCE(redeemed_by, claimed_by, recipient_id) IS NOT NULL", (user_id,))
        timeline_count = _safe_count(conn, "SELECT COUNT(1) AS n FROM user_audio_timeline WHERE user_id=?", (user_id,))
        last_audio = _safe_rows(conn, "SELECT event_type, title, platform, created_at FROM user_audio_timeline WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (user_id,))

    base_card = user_card(user_id) if user_id else {}
    sub = base_card.get("sub") or {}
    sub.update({
        "scope": payment.get("scope") or sub.get("scope"),
        "plan_type": payment.get("plan_type") or sub.get("plan_type"),
        "total_morning": payment.get("total_morning") if payment.get("total_morning") is not None else sub.get("total_morning"),
        "total_evening": payment.get("total_evening") if payment.get("total_evening") is not None else sub.get("total_evening"),
        "used_morning": payment.get("used_morning") if payment.get("used_morning") is not None else sub.get("used_morning"),
        "used_evening": payment.get("used_evening") if payment.get("used_evening") is not None else sub.get("used_evening"),
        "status": payment.get("subscription_status") or sub.get("status"),
    })
    repeat = _repeat_purchase_score(
        sub=sub,
        invited_count=int(base_card.get("invited_count") or 0),
        gift_created=gift_created,
        timeline_count=timeline_count,
    )
    return {
        "ok": True,
        "payment": payment,
        "user_card": base_card,
        "attribution": _attribution_from_event(first_event),
        "first_event": first_event,
        "time_to_payment": _human_delta(payment.get("joined_at"), payment.get("created_at") or payment.get("paid_at")),
        "gift_created": gift_created,
        "gift_redeemed": gift_redeemed,
        "timeline_count": timeline_count,
        "last_audio": last_audio[0] if last_audio else None,
        "repeat_purchase": repeat,
    }


def format_money_period(summary: dict[str, Any]) -> str:
    labels = {"today": "сегодня", "week": "за 7 дней", "month": "за 30 дней", "all": "за всё время"}
    period = str(summary.get("period") or "today")
    lines = [
        f"💰 Деньги и клиенты — {labels.get(period, period)}",
        "",
        f"Оплат: {summary.get('count', 0)}",
        f"Уникальных оплативших: {summary.get('paid_users', 0)}",
        f"Сумма: {_amount_rub(summary.get('amount'), 'RUB')}",
        f"Нужно проверить: {summary.get('problems', 0)}",
        "",
    ]
    rows = summary.get("rows") or []
    if not rows:
        lines.append("Оплат за этот период пока нет.")
        return "\n".join(lines)
    lines.append("Последние оплаты. Нажмите на оплату, чтобы открыть клиента:")
    for row in rows[:20]:
        name = " ".join(x for x in [str(row.get("first_name") or "").strip(), ("@" + str(row.get("username"))) if row.get("username") else ""] if x).strip()
        user = name or f"user_id {row.get('user_id')}"
        status = row.get("provider_status") or row.get("subscription_status") or "получена"
        problem = f" · проверить: {row.get('problem')}" if row.get("problem") else ""
        lines.append(f"• #{row.get('id')} — {user} — {_amount_rub(row.get('amount'), row.get('currency'))} — {status}{problem}")
    return "\n".join(lines)


def format_payment_client_card(card: dict[str, Any]) -> str:
    if not card.get("ok"):
        return "❌ Оплата не найдена."
    p = card["payment"]
    uc = card.get("user_card") or {}
    u = uc.get("user") or {}
    sub = uc.get("sub") or {}
    attribution = card.get("attribution") or {}
    repeat = card.get("repeat_purchase") or {}
    invited = int(uc.get("invited_count") or 0)
    used = int(sub.get("used_morning") or 0) + int(sub.get("used_evening") or 0)
    total = int(sub.get("total_morning") or 0) + int(sub.get("total_evening") or 0)
    last_audio = card.get("last_audio") or {}

    username = f"@{u.get('username')}" if u.get("username") else ""
    head_name = " ".join(x for x in [str(u.get("first_name") or "").strip(), username] if x) or f"user_id {p.get('user_id')}"
    lines = [
        f"👤 Клиент из оплаты #{p.get('id')}",
        f"Клиент: {head_name}",
        f"user_id: {p.get('user_id')}",
        "",
        "💰 Деньги",
        f"Оплата: {_amount_rub(p.get('amount'), p.get('currency'))}",
        f"Тариф: {sub.get('scope') or sub.get('plan_type') or p.get('payload') or 'не указан'}",
        f"Статус платежа: {p.get('provider_status') or 'получена'}",
        f"Проблема: {p.get('problem') or 'нет'}",
        f"Когда оплатил: {p.get('created_at') or '-'}",
        f"От /start до оплаты: {card.get('time_to_payment')}",
        "",
        "📣 Откуда пришёл",
        f"Источник: {attribution.get('source')}",
        f"Кампания: {attribution.get('campaign')}",
        f"Креатив: {attribution.get('creative')}",
        f"Расход на привлечение: {attribution.get('ad_spend')}",
        "",
        "🎧 Использование",
        f"Прослушано/выдано по подписке: {used}/{total}",
        f"Событий прослушивания в истории: {card.get('timeline_count')}",
        f"Последнее аудио: {last_audio.get('title') or 'нет данных'}",
        "",
        "🎁 Подарки и рекомендации",
        f"Подарков создано: {card.get('gift_created')}",
        f"Подарков активировано/передано: {card.get('gift_redeemed')}",
        f"Приглашено по реферальной записи: {invited}",
        "",
        "🔁 Повторная покупка",
        f"Вероятность: {repeat.get('label')}",
        f"Почему: {repeat.get('why')}",
        f"Что сделать: {repeat.get('action')}",
        "",
        "Важно: источник, креатив и рекламный расход показываются только если они уже записаны в событиях. Если там 'не подключено' — нужна отдельная интеграция UTM/рекламных расходов.",
    ]
    return "\n".join(lines)
