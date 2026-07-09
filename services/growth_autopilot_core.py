from __future__ import annotations

import re
from typing import Any

SAFE_AUTOPILOT_MODE = "read_only_plan_only"


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def pct(part: int, whole: int) -> float | None:
    if int(whole or 0) <= 0:
        return None
    return round(float(part) * 100.0 / float(whole), 1)


def money_rub_from_minor(amount_minor: int | float | None) -> str:
    return f"{safe_float(amount_minor) / 100:.0f} ₽"


def parse_ad_spend_to_minor(value: Any) -> int | None:
    """Parse existing human-entered ad_spend labels into minor units.

    This is compatibility parsing for manual marketing evidence, not accounting.
    """

    text = str(value or "").strip().lower()
    if not text:
        return None
    normalized = text.replace("рублей", "rub").replace("руб.", "rub").replace("руб", "rub")
    match = re.search(r"(\d+(?:[\s_.]\d{3})*(?:[,.]\d{1,2})?|\d+)", normalized)
    if not match:
        return None
    number = match.group(1).replace(" ", "").replace("_", "")
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


def data_gaps(*, ad_links: dict[str, Any], payments: dict[str, Any], funnel: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    if safe_int(ad_links.get("links")) == 0:
        gaps.append("Нет рекламных tracking-ссылок: невозможно связать канал/кампанию/креатив с оплатой.")
    if safe_int(ad_links.get("without_spend")) > 0:
        gaps.append("Есть рекламные ссылки без расхода: CAC/ROMI будут неполными.")
    if safe_int(funnel.get("start_users")) == 0:
        gaps.append("Нет /start-событий за период: click→start и start→payment пока не считаются.")
    if str(payments.get("status_source")) in {"missing_table", "error"}:
        gaps.append("Платёжная таблица недоступна для Growth Autopilot snapshot.")
    return gaps


def data_confidence(*, ad_links: dict[str, Any], payments: dict[str, Any], funnel: dict[str, Any]) -> str:
    gaps = data_gaps(ad_links=ad_links, payments=payments, funnel=funnel)
    if len(gaps) >= 3:
        return "low"
    if gaps:
        return "medium"
    return "high"


def recommendation(
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


def _paid_users_from_snapshot(*, funnel: dict[str, Any], payments: dict[str, Any]) -> int:
    if "paid_users" in funnel:
        return safe_int(funnel.get("paid_users"))
    if "paid_users" in payments:
        return safe_int(payments.get("paid_users"))
    return safe_int(payments.get("payments"))


def diagnose_growth_snapshot(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    funnel = dict(snapshot.get("funnel") or {})
    payments = dict(snapshot.get("payments") or {})
    ad_links = dict(snapshot.get("ad_links") or {})
    access = dict(snapshot.get("access_alerts") or {})
    dq = dict(snapshot.get("data_quality") or {})

    recs: list[dict[str, Any]] = []
    confidence = str(dq.get("confidence") or "low")

    access_count = safe_int(access.get("count"))
    if access_count > 0:
        recs.append(recommendation(
            priority="red",
            kind="payment_access_guard",
            title="Деньги есть, но доступ не найден",
            evidence=[f"Проблемных оплат без активного доступа: {access_count}"],
            action="Сначала проверить выдачу доступа. Пока не масштабировать рекламу по этому периоду.",
            confidence="high",
            risk="high",
        ))

    if safe_int(ad_links.get("links")) == 0 or safe_int(ad_links.get("without_spend")) > 0:
        recs.append(recommendation(
            priority="yellow",
            kind="data_quality",
            title="Закрыть дыры в рекламной разметке",
            evidence=[
                f"tracking-ссылок: {safe_int(ad_links.get('links'))}",
                f"без расхода: {safe_int(ad_links.get('without_spend'))}",
            ],
            action="Создавать все кампании через рекламные ссылки и вносить расход/креатив. Без этого CAC/ROMI будут слепыми.",
            confidence="high",
        ))

    start_users = safe_int(funnel.get("start_users"))
    demo_sent = safe_int(funnel.get("demo_sent_users"))
    demo_ack = safe_int(funnel.get("demo_ack_users"))
    tariff_open = safe_int(funnel.get("tariff_open_users"))
    paid = _paid_users_from_snapshot(funnel=funnel, payments=payments)
    revenue_minor = safe_int(payments.get("revenue_minor"))

    start_to_demo = pct(demo_sent, start_users)
    if start_users >= 20 and start_to_demo is not None and start_to_demo < 35:
        recs.append(recommendation(
            priority="yellow",
            kind="start_to_demo_drop",
            title="Много входов, мало демо",
            evidence=[f"/start: {start_users}", f"demo_sent: {demo_sent}", f"start→demo: {start_to_demo}%"],
            action="Проверить первый экран, кнопку демо и обещание в рекламном креативе.",
            confidence=confidence,
        ))

    if demo_ack >= 10 and paid == 0:
        recs.append(recommendation(
            priority="red",
            kind="creative_offer_mismatch",
            title="Демо слушают, но оплат нет",
            evidence=[f"demo_ack: {demo_ack}", f"paid_users: {paid}"],
            action="Не увеличивать бюджет. Сгенерировать новые креативы и post-demo оффер, запустить A/B только с тестовым лимитом.",
            confidence=confidence,
            risk="medium",
        ))

    if tariff_open >= 5 and paid == 0:
        recs.append(recommendation(
            priority="red",
            kind="tariff_to_payment_drop",
            title="Тарифы открывают, но не платят",
            evidence=[f"tariff_open: {tariff_open}", f"paid_users: {paid}"],
            action="Проверить цену, доверие, платёжный UX и текст перед оплатой. Не винить рекламу до проверки оплаты.",
            confidence=confidence,
            risk="medium",
        ))

    if paid >= 3 and revenue_minor > 0 and access_count == 0:
        recs.append(recommendation(
            priority="green",
            kind="scale_candidate",
            title="Есть оплаты — можно искать масштабирование",
            evidence=[f"paid_users: {paid}", f"revenue: {money_rub_from_minor(revenue_minor)}", f"access_alerts: {access_count}"],
            action="Показать лучшие источники/креативы, проверить CAC и только затем предложить +10–15% бюджета в каналах с высокой достоверностью данных.",
            confidence=confidence,
        ))

    if not recs:
        recs.append(recommendation(
            priority="white",
            kind="observe_more",
            title="Данных пока мало — работаем в режиме наблюдения",
            evidence=[f"/start: {start_users}", f"demo_ack: {demo_ack}", f"paid_users: {paid}"],
            action="Продолжать сбор событий, закрыть разметку расходов и не включать автоуправление бюджетом.",
            confidence=confidence,
        ))

    recs.append(recommendation(
        priority="white",
        kind="autopilot_safety_contract",
        title="Автопилот v0 ничего не применяет сам",
        evidence=["external_writes_enabled=False", "budget_writes_enabled=False", "conversion_postbacks_enabled=False"],
        action="Следующий этап — Action Inbox с подтверждением, затем guarded apply с лимитами.",
        confidence="high",
    ))
    return recs


def _slug(value: Any, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    safe = re.sub(r"[^a-z0-9_:-]+", "_", raw).strip("_")
    return safe[:48] or fallback


def build_growth_action_cards(recommendations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build read-only action inbox cards from Growth Autopilot recommendations.

    Cards are navigation/evidence objects only. They intentionally carry the same
    manual-review safety contract as source recommendations and cannot be applied
    automatically.
    """

    cards: list[dict[str, Any]] = []
    for idx, rec in enumerate(recommendations or [], 1):
        kind = _slug(rec.get("kind"), f"action_{idx}")
        priority = str(rec.get("priority") or "white")
        card = {
            "id": f"ga:{idx}:{kind}",
            "idx": idx,
            "kind": kind,
            "priority": priority,
            "title": str(rec.get("title") or "Growth action"),
            "evidence": [str(x) for x in list(rec.get("evidence") or [])[:6]],
            "recommended_action": str(rec.get("recommended_action") or "Открыть рекомендацию и проверить вручную."),
            "confidence": str(rec.get("confidence") or "low"),
            "risk": str(rec.get("risk") or "low"),
            "apply_mode": "manual_review_required",
            "autopilot_can_apply_now": False,
        }
        cards.append(card)
    return cards


def find_growth_action_card(cards: list[dict[str, Any]], card_id: str | None) -> dict[str, Any] | None:
    wanted = str(card_id or "")
    wanted_idx = None
    match = re.match(r"^ga:(\d+)$", wanted)
    if match:
        wanted_idx = safe_int(match.group(1))
    for card in cards or []:
        if str(card.get("id")) == wanted:
            return card
        if wanted_idx is not None and safe_int(card.get("idx")) == wanted_idx:
            return card
    return None


def format_growth_action_inbox(cards: list[dict[str, Any]], *, period: str) -> str:
    lines = [
        "📥 Growth Action Inbox",
        "read-only: карточки действий → доказательства → ручная проверка",
        "",
        f"Период: {period}",
        f"Карточек: {len(cards)}",
        "",
    ]
    if not cards:
        lines.append("Пока нет действий. Автопилот ничего не применяет сам.")
        return "\n".join(lines).strip()
    for card in cards[:10]:
        lines.extend([
            f"{safe_int(card.get('idx'))}. {priority_icon(str(card.get('priority')))} {card.get('title')}",
            f"   Тип: {card.get('kind')} | уверенность: {card.get('confidence')} | риск: {card.get('risk')}",
            f"   Режим: {card.get('apply_mode')}",
            "",
        ])
    lines.extend([
        "🛡 Safety lock",
        "— карточки не меняют бюджеты;",
        "— не отправляют postbacks;",
        "— не меняют тарифы/воронку;",
        "— любые действия только вручную после проверки.",
    ])
    return "\n".join(lines).strip()


def format_growth_action_card(card: dict[str, Any] | None, *, period: str) -> str:
    if not card:
        return "📥 Growth Action Inbox\n\nКарточка не найдена. Вернитесь к списку действий."
    lines = [
        "📥 Growth Action",
        "",
        f"Период: {period}",
        f"Приоритет: {priority_icon(str(card.get('priority')))} {card.get('priority')}",
        f"Тип: {card.get('kind')}",
        f"Название: {card.get('title')}",
        f"Уверенность: {card.get('confidence')}",
        f"Риск: {card.get('risk')}",
        "",
        "Что сделать вручную:",
        str(card.get("recommended_action") or "Проверить вручную."),
        "",
        "Доказательства:",
    ]
    evidence = list(card.get("evidence") or [])
    if evidence:
        for item in evidence[:8]:
            lines.append(f"— {item}")
    else:
        lines.append("— доказательств пока нет")
    lines.extend([
        "",
        "Safety:",
        f"— apply_mode={card.get('apply_mode')}",
        f"— autopilot_can_apply_now={card.get('autopilot_can_apply_now')}",
    ])
    return "\n".join(lines).strip()


def fmt_pct(value: Any) -> str:
    return "—" if value is None else f"{float(value):.1f}%"


def priority_icon(priority: str) -> str:
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

    paid_users = _paid_users_from_snapshot(funnel=funnel, payments=payments)
    payment_rows = safe_int(payments.get("payments"))

    lines = [
        "🤖 Growth Autopilot v0",
        "read-only: анализ → рекомендации → доказательства",
        "",
        f"Период: {snapshot.get('period')}",
        f"Режим: {snapshot.get('autopilot_mode')}",
        f"Достоверность данных: {dq.get('confidence', 'low')}",
        "",
        "📊 Воронка",
        f"— рекламные клики: {safe_int(funnel.get('ad_clicks'))}",
        f"— /start: {safe_int(funnel.get('start_users'))} ({fmt_pct(funnel.get('click_to_start_pct'))} от кликов)",
        f"— демо отправлено: {safe_int(funnel.get('demo_sent_users'))} ({fmt_pct(funnel.get('start_to_demo_pct'))} от /start)",
        f"— демо подтверждено: {safe_int(funnel.get('demo_ack_users'))} ({fmt_pct(funnel.get('demo_to_ack_pct'))} от demo)",
        f"— тарифы открыли: {safe_int(funnel.get('tariff_open_users'))} ({fmt_pct(funnel.get('ack_to_tariff_pct'))} от ack)",
        f"— оплатили пользователей: {paid_users} ({fmt_pct(funnel.get('start_to_paid_pct'))} от /start)",
        "",
        "💰 Деньги / реклама",
        f"— успешных платежей: {payment_rows}",
        f"— выручка: {money_rub_from_minor(safe_int(payments.get('revenue_minor')))}",
        f"— рекламных ссылок: {safe_int(ad_links.get('links'))}",
        f"— ссылок без расхода: {safe_int(ad_links.get('without_spend'))}",
        f"— расход из ссылок (низкая достоверность): {money_rub_from_minor(safe_int(ad_links.get('spend_minor_low_confidence')))}",
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
            f"{idx}. {priority_icon(str(rec.get('priority')))} {rec.get('title')}",
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
