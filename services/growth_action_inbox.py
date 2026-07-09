from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.growth_autopilot_core import priority_icon, safe_int


_PRIORITY_ORDER = {"red": 0, "yellow": 1, "green": 2, "white": 3}
_KIND_ACTION_TYPE = {
    "payment_access_guard": "fix_access",
    "data_quality": "fix_tracking",
    "start_to_demo_drop": "fix_onboarding",
    "creative_offer_mismatch": "test_offer",
    "tariff_to_payment_drop": "fix_payment_path",
    "scale_candidate": "review_scale",
    "observe_more": "collect_data",
    "autopilot_safety_contract": "safety_note",
}


@dataclass(frozen=True)
class GrowthActionItem:
    action_id: str
    priority: str
    action_type: str
    title: str
    recommended_action: str
    evidence: list[str]
    confidence: str
    risk: str
    apply_mode: str
    autopilot_can_apply_now: bool


def _slug(value: Any) -> str:
    text = str(value or "item").strip().lower()
    out = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "_":
            out.append("_")
    slug = "".join(out).strip("_")
    return slug or "item"


def build_action_inbox(snapshot: dict[str, Any], *, limit: int = 8) -> list[GrowthActionItem]:
    """Convert Growth Autopilot recommendations into a read-only action inbox.

    This function is deliberately pure: no database writes, no external calls, no
    action execution. It only normalizes existing evidence into stable admin cards.
    """

    recommendations = list(snapshot.get("recommendations") or [])
    period = _slug(snapshot.get("period") or "today")
    items: list[GrowthActionItem] = []
    for idx, rec in enumerate(recommendations, 1):
        priority = str(rec.get("priority") or "white")
        kind = str(rec.get("kind") or "unknown")
        evidence = [str(x) for x in list(rec.get("evidence") or [])[:6]]
        items.append(
            GrowthActionItem(
                action_id=f"growth_{period}_{idx}_{_slug(kind)}",
                priority=priority,
                action_type=_KIND_ACTION_TYPE.get(kind, "manual_review"),
                title=str(rec.get("title") or kind),
                recommended_action=str(rec.get("recommended_action") or "Проверить вручную."),
                evidence=evidence,
                confidence=str(rec.get("confidence") or "low"),
                risk=str(rec.get("risk") or "low"),
                apply_mode=str(rec.get("apply_mode") or "manual_review_required"),
                autopilot_can_apply_now=bool(rec.get("autopilot_can_apply_now") is True),
            )
        )

    items.sort(key=lambda item: (_PRIORITY_ORDER.get(item.priority, 99), item.action_id))
    return items[: max(1, safe_int(limit) or 8)]


def action_inbox_summary(items: list[GrowthActionItem]) -> dict[str, int]:
    out = {"red": 0, "yellow": 0, "green": 0, "white": 0, "total": len(items), "auto_apply": 0}
    for item in items:
        if item.priority in out:
            out[item.priority] += 1
        if item.autopilot_can_apply_now:
            out["auto_apply"] += 1
    return out


def format_action_inbox(snapshot: dict[str, Any], *, limit: int = 8) -> str:
    items = build_action_inbox(snapshot, limit=limit)
    summary = action_inbox_summary(items)
    period = snapshot.get("period") or "today"
    lines = [
        "📌 Growth Action Inbox v1",
        "read-only: задачи → доказательства → ручное решение",
        "",
        f"Период: {period}",
        f"Всего задач: {summary['total']} | 🔴 {summary['red']} | 🟡 {summary['yellow']} | 🟢 {summary['green']} | ⚪ {summary['white']}",
        f"Автоприменение: {summary['auto_apply']}",
        "",
    ]
    for idx, item in enumerate(items, 1):
        lines.extend(
            [
                f"{idx}. {priority_icon(item.priority)} {item.title}",
                f"   Тип: {item.action_type}",
                f"   Что сделать: {item.recommended_action}",
                f"   Уверенность: {item.confidence} | риск: {item.risk}",
                f"   Режим: {item.apply_mode} | auto_apply={item.autopilot_can_apply_now}",
            ]
        )
        if item.evidence:
            lines.append("   Доказательства: " + "; ".join(item.evidence[:4]))
        lines.append("")

    lines.extend(
        [
            "🛡 Safety lock",
            "— Action Inbox ничего не применяет сам;",
            "— не меняет бюджеты, тарифы, postbacks и пользовательскую воронку;",
            "— следующий этап: кнопки подтверждения только через guarded apply gateway.",
        ]
    )
    return "\n".join(lines).strip()
