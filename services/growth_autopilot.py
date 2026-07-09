from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from services.db import db

log = logging.getLogger(__name__)

_PERIOD_DAYS: dict[str, int | None] = {
    "today": 0,
    "week": 7,
    "month": 30,
    "all": None,
}

_SAFE_AUTOPILOT_MODE = "read_only_plan_only"


# ---------------------------------------------------------------------------
# Small, defensive read helpers. Growth Autopilot v0 must never break the bot if
# an optional analytics table/column is absent in an older deployment.
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _period_start(period: str) -> str | None:
    normalized = (period or "today").strip().lower()
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


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _pct(part: int, whole: int) -> float | None:
    if int(whole or 0) <= 0:
        return None
    return round(float(part) * 100.0 / float(whole), 1)


def _money_rub_from_minor(amount_minor: int | float | None) -> str:
    return f"{_safe_float(amount_minor) / 100:.0f} ₽"


def parse_ad_spend_to_minor(value: Any) -> int | None:
    """Best-effort parser for existing human-entered ad_spend values.

    The old admin links intentionally allowed labels like "340rub" or
    "340 RUB".  Growth Autopilot v0 keeps that compatibility and treats the
    parsed result as low-confidence evidence, not accounting truth.
    """

    text = str(value or "").strip().lower()
    if not text:
        return None
    normalized = text.replace("рублей", "rub").replace("руб.", "rub").replace("руб", "rub")
    match = re.search(r"(\d+(?:[\s_.]\d{3})*(?:[,.]\d{1,2})?|\d+)", normalized)
    if not match:
        return None
    number = match.group(1).replace(" ", "").replace("_", "")
    # 1.234 in Russian ad labels is more often a thousands separator than a
    # decimal.  Comma is treated as decimal separator.
    if "," in number:
        number = number.replace(".", "").replace(",", ".")
    elif number.count(".") == 1 and len(number.split(".")[-1]) == 3:
        number = number.replace(".", "")
    try:
        rub = float(number)
    except ValueError:
        return None
    if rub < 0:
        return None
    return int(round(rub * 100))


def _table_columns(conn: Any, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        log.debug("table info read failed for %s", table, exc_info=True)
        return set()


def _fetch_rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        with db() as conn:
            return _rows(conn.execute(sql, params).fetchall())
    except Exception:
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
            return _safe_int(row[keys[0]]) if keys else 0
        return _safe_int(row[0])
    except Exception:
        log.debug("growth autopilot scalar query skipped", exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Snapshot builders
# ---------------------------------------------------------------------------

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
    if start:
        where = "WHERE name=? AND COALESCE(created_at, '') >= ?"
    else:
        where = "WHERE name=?"
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
                return {"payments": 0, "revenue_minor": 0, "currency": "RUB", "status_source": "missing_table"}

            status_col = "provider_status" if "provider_status" in cols else ("status" if "status" in cols else "")
            amount_col = "amount" if "amount" in cols else ("amount_minor" if "amount_minor" in cols else "")
            created_col = "created_at" if "created_at" in cols else ("paid_at" if "paid_at" in cols else "")
            currency_col = "currency" if "currency" in cols else ""

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
                f"SELECT COUNT(*) AS payments, SUM({amount_expr}) AS revenue_minor, {currency_expr} AS currency FROM payments {where}",
                tuple(params),
            ).fetchone()
            data = _rowdict(row) or {}
            return {
                "payments": _safe_int(data.get("payments")),
                "revenue_minor": _safe_int(data.get("revenue_minor")),
                "currency": str(data.get("currency") or "RUB"),
                "status_source": status_col or "none",
            }
    except Exception:
        log.debug("payment summary failed", exc_info=True)
        return {"payments": 0, "revenue_minor": 0, "currency": "RUB", "status_source": "error"}


def _ad_link_summary(limit: int = 50) -> dict[str, Any]:
    rows = _fetch_rows(
        """
        SELECT source, campaign, creative, ad_spend, start_payload, url, created_at
        FROM admin_ad_links
        ORDER BY id DESC
        LIMIT ?
        """.strip(),
        (int(limit),),
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


def _safe_access_alerts() -> list[dict[str, Any]]:
    try:
        from services.admin_growth_ops import access_alerts

        return list(access_alerts(limit=20) or [])
    except Exception:
        log.debug("access alerts unavailable", exc_info=True)
        return []


def _safe_segments() -> dict[str, int]:
    try:
        from services.segments import segment_counts

        return {str(k): _safe_int(v) for k, v in (segment_counts(limit_users=5000) or {}).items()}
    except Exception:
        log.debug("segment counts unavailable", exc_info=True)
        return {}


def _safe_funnel2() -> dict[str, Any]:
    try:
        from services.funnel2_analytics import scenario_counts

        return dict(scenario_counts() or {})
    except Exception:
        log.debug("funnel2 counts unavailable", exc_info=True)
        return {}


def build_growth_autopilot_snapshot(period: str = "today") -> dict[str, Any]:
    """Build a read-only Growth Autopilot evidence snapshot.

    No writes, no external calls, no budget mutations.  This is the first safe
    layer that later autonomous decisions can use as evidence.
    """

    period = normalize_period(period)
    events = _event_counts(period)
    demo = _demo_counts(period)
    payments = _payment_summary(period)
    ad_links = _ad_link_summary()
    access_alert_rows = _safe_access_alerts()
    segments = _safe_segments()
    funnel2 = _safe_funnel2()

    start_users = max(events.get("funnel_start_command", 0), 0)
    demo_sent = max(demo.get("sent_users", 0), events.get("demo_sent", 0))
    demo_ack = max(demo.get("ack_users", 0), events.get("demo_ack", 0))
    tariff_open = max(events.get("sub_menu_open", 0), events.get("funnel_tariffs_command", 0))
    pay_click = events.get("payment_started", 0)
    paid = _safe_int(payments.get("payments"))

    funnel = {
        "start_users": start_users,
        "demo_sent_users": demo_sent,
        "demo_ack_users": demo_ack,
        "tariff_open_users": tariff_open,
        "payment_started_users": pay_click,
        "paid_users": paid,
        "start_to_demo_pct": _pct(demo_sent, start_users),
        "demo_to_ack_pct": _pct(demo_ack, demo_sent),
        "ack_to_tariff_pct": _pct(tariff_open, demo_ack),
        "tariff_to_paid_pct": _pct(paid, tariff_open),
        "start_to_paid_pct": _pct(paid, start_users),
    }

    data_quality = {
        "mode": _SAFE_AUTOPILOT_MODE,
        "external_writes_enabled": False,
        "budget_writes_enabled": False,
        "conversion_postbacks_enabled": False,
        "confidence": _data_confidence(ad_links=ad_links, payments=payments, funnel=funnel),
        "gaps": _data_gaps(ad_links=ad_links, payments=payments, funnel=funnel),
    }

    snapshot = {
        "ok": True,
        "period": period,
        "generated_at_utc": _utc_now().isoformat(),
        "autopilot_mode": _SAFE_AUTOPILOT_MODE,
        "data_quality": data_quality,
        "ad_links": ad_links,
        "funnel": funnel,
        "payments": payments,
        "access_alerts": {"count": len(access_alert_rows), "rows": access_alert_rows[:5]},
        "segments": segments,
        "funnel2": funnel2,
    }
    snapshot["recommendations"] = diagnose_growth_snapshot(snapshot)
    return snapshot


# ---------------------------------------------------------------------------
# Deterministic recommendation layer.  It deliberately returns advisory items,
# not executable actions.  Later versions can route selected proposals through a
# guarded action gateway.
# ---------------------------------------------------------------------------

def _data_gaps(*, ad_links: dict[str, Any], payments: dict[str, Any], funnel: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    if _safe_int(ad_links.get("links")) == 0:
        gaps.append("Нет рекламных tracking-ссылок: невозможно связать канал/кампанию/креатив с оплатой.")
    if _safe_int(ad_links.get("without_spend")) > 0:
        gaps.append("Есть рекламные ссылки без расхода: CAC/ROMI будут неполными.")
    if _safe_int(funnel.get("start_users")) == 0:
        gaps.append("Нет /start-событий за период: click→start и start→payment пока не считаются.")
    if str(payments.get("status_source")) in {"missing_table", "error"}:
        gaps.append("Платёжная таблица недоступна для Growth Autopilot snapshot.")
    return gaps


def _data_confidence(*, ad_links: dict[str, Any], payments: dict[str, Any], funnel: dict[str, Any]) -> str:
    gaps = _data_gaps(ad_links=ad_links, payments=payments, funnel=funnel)
    if len(gaps) >= 3:
        return "low"
    if gaps:
        return "medium"
    return "high"


def _recommendation(
    *,
    priority: str,
    kind: str,
    title: str,
    evidence: list[str],
    action: str,
    confidence: str,
    risk: str = "low",
) -> dict[str, Any]:
    return {
        "priority": priority,
        "kind": kind,
        "title": title,
        "evidence": evidence,
        "recommended_action": action,
        "confidence": confidence,
        "risk": risk,
        "apply_mode": "manual_review_required",
        "autopilot_can_apply_now": False,
    }


def diagnose_growth_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    funnel = dict(snapshot.get("funnel") or {})
    payments = dict(snapshot.get("payments") or {})
    ad_links = dict(snapshot.get("ad_links") or {})
    access = dict(snapshot.get("access_alerts") or {})
    dq = dict(snapshot.get("data_quality") or {})

    recs: list[dict[str, Any]] = []
    confidence = str(dq.get("confidence") or "low")

    access_count = _safe_int(access.get("count"))
    if access_count > 0:
        recs.append(_recommendation(
            priority="red",
            kind="payment_access_guard",
            title="Деньги есть, но доступ не найден",
            evidence=[f"Проблемных оплат без активного доступа: {access_count}"],
            action="Сначала проверить выдачу доступа. Пока не масштабировать рекламу по этому периоду.",
            confidence="high",
            risk="high",
        ))

    if _safe_int(ad_links.get("links")) == 0 or _safe_int(ad_links.get("without_spend")) > 0:
        recs.append(_recommendation(
            priority="yellow",
            kind="data_quality",
            title="Закрыть дыры в рекламной разметке",
            evidence=[
                f"tracking-ссылок: {_safe_int(ad_links.get('links'))}",
                f"без расхода: {_safe_int(ad_links.get('without_spend'))}",
            ],
            action="Создавать все кампании через рекламные ссылки и вносить расход/креатив. Без этого CAC/ROMI будут слепыми.",
            confidence="high",
        ))

    start_users = _safe_int(funnel.get("start_users"))
    demo_sent = _safe_int(funnel.get("demo_sent_users"))
    demo_ack = _safe_int(funnel.get("demo_ack_users"))
    tariff_open = _safe_int(funnel.get("tariff_open_users"))
    paid = _safe_int(funnel.get("paid_users"))
    revenue_minor = _safe_int(payments.get("revenue_minor"))

    if start_users >= 20 and _pct(demo_sent, start_users) is not None and (_pct(demo_sent, start_users) or 0) < 35:
        recs.append(_recommendation(
            priority="yellow",
            kind="start_to_demo_drop",
            title="Много входов, мало демо",
            evidence=[f"/start: {start_users}", f"demo_sent: {demo_sent}", f"start→demo: {_pct(demo_sent, start_users)}%"],
            action="Проверить первый экран, кнопку демо и обещание в рекламном креативе.",
            confidence=confidence,
        ))

    if demo_ack >= 10 and paid == 0:
        recs.append(_recommendation(
            priority="red",
            kind="creative_offer_mismatch",
            title="Демо слушают, но оплат нет",
            evidence=[f"demo_ack: {demo_ack}", f"paid: {paid}"],
            action="Не увеличивать бюджет. Сгенерировать новые креативы и post-demo оффер, запустить A/B только с тестовым лимитом.",
            confidence=confidence,
            risk="medium",
        ))

    if tariff_open >= 5 and paid == 0:
        recs.append(_recommendation(
            priority="red",
            kind="tariff_to_payment_drop",
            title="Тарифы открывают, но не платят",
            evidence=[f"tariff_open: {tariff_open}", f"paid: {paid}"],
            action="Проверить цену, доверие, платёжный UX и текст перед оплатой. Не винить рекламу до проверки оплаты.",
            confidence=confidence,
            risk="medium",
        ))

    if paid >= 3 and revenue_minor > 0 and access_count == 0:
        recs.append(_recommendation(
            priority="green",
            kind="scale_candidate",
            title="Есть оплаты — можно искать масштабирование",
            evidence=[f"paid: {paid}", f"revenue: {_money_rub_from_minor(revenue_minor)}", f"access_alerts: {access_count}"],
            action="Показать лучшие источники/креативы, проверить CAC и только затем предложить +10–15% бюджета в каналах с высокой достоверностью данных.",
            confidence=confidence,
        ))

    if not recs:
        recs.append(_recommendation(
            priority="white",
            kind="observe_more",
            title="Данных пока мало — работаем в режиме наблюдения",
            evidence=[f"/start: {start_users}", f"demo_ack: {demo_ack}", f"paid: {paid}"],
            action="Продолжать сбор событий, закрыть разметку расходов и не включать автоуправление бюджетом.",
            confidence=confidence,
        ))

    recs.append(_recommendation(
        priority="white",
        kind="autopilot_safety_contract",
        title="Автопилот v0 ничего не применяет сам",
        evidence=["external_writes_enabled=False", "budget_writes_enabled=False", "conversion_postbacks_enabled=False"],
        action="Следующий этап — Action Inbox с подтверждением, затем guarded apply с лимитами.",
        confidence="high",
    ))
    return recs


# ---------------------------------------------------------------------------
# Admin text formatter
# ---------------------------------------------------------------------------

def _fmt_pct(value: Any) -> str:
    return "—" if value is None else f"{float(value):.1f}%"


def _priority_icon(priority: str) -> str:
    return {
        "red": "🔴",
        "yellow": "🟡",
        "green": "🟢",
        "white": "⚪",
    }.get(str(priority), "⚪")


def format_growth_autopilot_report(snapshot: dict[str, Any]) -> str:
    funnel = dict(snapshot.get("funnel") or {})
    payments = dict(snapshot.get("payments") or {})
    ad_links = dict(snapshot.get("ad_links") or {})
    dq = dict(snapshot.get("data_quality") or {})
    recs = list(snapshot.get("recommendations") or [])

    lines = [
        "🤖 Growth Autopilot v0",
        "read-only: анализ → рекомендации → доказательства",
        "",
        f"Период: {snapshot.get('period')}",
        f"Режим: {snapshot.get('autopilot_mode')}",
        f"Достоверность данных: {dq.get('confidence', 'low')}",
        "",
        "📊 Воронка",
        f"— /start: {_safe_int(funnel.get('start_users'))}",
        f"— демо отправлено: {_safe_int(funnel.get('demo_sent_users'))} ({_fmt_pct(funnel.get('start_to_demo_pct'))} от /start)",
        f"— демо подтверждено: {_safe_int(funnel.get('demo_ack_users'))} ({_fmt_pct(funnel.get('demo_to_ack_pct'))} от demo)",
        f"— тарифы открыли: {_safe_int(funnel.get('tariff_open_users'))} ({_fmt_pct(funnel.get('ack_to_tariff_pct'))} от ack)",
        f"— оплатили: {_safe_int(funnel.get('paid_users'))} ({_fmt_pct(funnel.get('start_to_paid_pct'))} от /start)",
        "",
        "💰 Деньги / реклама",
        f"— выручка: {_money_rub_from_minor(_safe_int(payments.get('revenue_minor')))}",
        f"— рекламных ссылок: {_safe_int(ad_links.get('links'))}",
        f"— ссылок без расхода: {_safe_int(ad_links.get('without_spend'))}",
        f"— расход из ссылок (низкая достоверность): {_money_rub_from_minor(_safe_int(ad_links.get('spend_minor_low_confidence')))}",
        "",
    ]

    gaps = list(dq.get("gaps") or [])
    if gaps:
        lines.append("🧩 Дыры в данных")
        for gap in gaps[:6]:
            lines.append(f"— {gap}")
        lines.append("")

    lines.append("📌 План действий")
    for idx, rec in enumerate(recs[:8], 1):
        evidence = list(rec.get("evidence") or [])
        lines.extend([
            f"{idx}. {_priority_icon(str(rec.get('priority')))} {rec.get('title')}",
            f"   Что сделать: {rec.get('recommended_action')}",
            f"   Уверенность: {rec.get('confidence')} | риск: {rec.get('risk')}",
        ])
        if evidence:
            lines.append("   Доказательства: " + "; ".join(str(x) for x in evidence[:4]))
        lines.append("")

    lines.extend([
        "🛡 Защита от регрессий",
        "— модуль только читает БД;",
        "— бюджеты и рекламные кабинеты не меняет;",
        "— конверсии наружу не отправляет;",
        "— пользовательские сценарии демо/оплат/тарифов не затрагивает.",
    ])
    return "\n".join(lines).strip()


def build_growth_autopilot_report(period: str = "today") -> str:
    return format_growth_autopilot_report(build_growth_autopilot_snapshot(period))
