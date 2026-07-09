from services.growth_action_inbox import build_action_inbox, format_action_inbox


def _snapshot():
    return {
        "period": "today",
        "recommendations": [
            {
                "priority": "yellow",
                "kind": "data_quality",
                "title": "Закрыть дыры в рекламной разметке",
                "recommended_action": "Внести расход и креатив.",
                "evidence": ["tracking-ссылок: 3", "без расхода: 2"],
                "confidence": "high",
                "risk": "low",
                "apply_mode": "manual_review_required",
                "autopilot_can_apply_now": False,
            },
            {
                "priority": "red",
                "kind": "payment_access_guard",
                "title": "Деньги есть, но доступ не найден",
                "recommended_action": "Проверить выдачу доступа.",
                "evidence": ["Проблемных оплат без активного доступа: 1"],
                "confidence": "high",
                "risk": "high",
                "apply_mode": "manual_review_required",
                "autopilot_can_apply_now": False,
            },
        ],
    }


def test_action_inbox_sorts_red_before_yellow_and_keeps_stable_ids():
    items = build_action_inbox(_snapshot())

    assert [item.priority for item in items[:2]] == ["red", "yellow"]
    assert items[0].action_id == "growth_today_2_payment_access_guard"
    assert items[0].action_type == "fix_access"
    assert items[1].action_type == "fix_tracking"


def test_action_inbox_never_enables_autopilot_apply_from_v1_cards():
    items = build_action_inbox(_snapshot())

    assert all(item.apply_mode == "manual_review_required" for item in items)
    assert all(item.autopilot_can_apply_now is False for item in items)


def test_format_action_inbox_contains_safety_lock_and_evidence():
    text = format_action_inbox(_snapshot())

    assert "Growth Action Inbox v1" in text
    assert "read-only" in text
    assert "Деньги есть, но доступ не найден" in text
    assert "Проблемных оплат без активного доступа: 1" in text
    assert "Action Inbox ничего не применяет сам" in text
