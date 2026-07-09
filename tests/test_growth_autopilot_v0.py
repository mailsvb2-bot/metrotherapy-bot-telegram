from services.growth_autopilot_core import (
    build_growth_action_cards,
    diagnose_growth_snapshot,
    find_growth_action_card,
    format_growth_action_card,
    format_growth_action_inbox,
    parse_ad_spend_to_minor,
)


def test_parse_ad_spend_to_minor_keeps_legacy_human_labels():
    assert parse_ad_spend_to_minor("340rub") == 34000
    assert parse_ad_spend_to_minor("1 250 RUB") == 125000
    assert parse_ad_spend_to_minor("1.250 руб") == 125000
    assert parse_ad_spend_to_minor("") is None


def test_growth_autopilot_recommendations_are_read_only_and_guarded():
    snapshot = {
        "funnel": {
            "start_users": 180,
            "demo_sent_users": 97,
            "demo_ack_users": 61,
            "tariff_open_users": 10,
            "paid_users": 0,
        },
        "payments": {"payments": 0, "revenue_minor": 0},
        "ad_links": {"links": 3, "with_spend": 1, "without_spend": 2},
        "access_alerts": {"count": 0},
        "data_quality": {"confidence": "medium"},
    }

    recs = diagnose_growth_snapshot(snapshot)

    assert any(r["kind"] == "creative_offer_mismatch" for r in recs)
    assert any(r["kind"] == "data_quality" for r in recs)
    assert all(r["autopilot_can_apply_now"] is False for r in recs)
    assert all(r["apply_mode"] == "manual_review_required" for r in recs)


def test_growth_autopilot_prioritizes_payment_access_guard():
    snapshot = {
        "funnel": {
            "start_users": 50,
            "demo_sent_users": 30,
            "demo_ack_users": 20,
            "tariff_open_users": 12,
            "paid_users": 5,
        },
        "payments": {"payments": 5, "revenue_minor": 500000},
        "ad_links": {"links": 5, "with_spend": 5, "without_spend": 0},
        "access_alerts": {"count": 2},
        "data_quality": {"confidence": "high"},
    }

    recs = diagnose_growth_snapshot(snapshot)

    first = recs[0]
    assert first["priority"] == "red"
    assert first["kind"] == "payment_access_guard"
    assert "не масштабировать" in first["recommended_action"].lower()


def test_growth_action_inbox_keeps_manual_review_safety_contract():
    recs = [
        {
            "priority": "red",
            "kind": "payment_access_guard",
            "title": "Деньги есть, но доступ не найден",
            "evidence": ["alerts=2"],
            "recommended_action": "Проверить доступ вручную.",
            "confidence": "high",
            "risk": "high",
        }
    ]

    cards = build_growth_action_cards(recs)

    assert cards[0]["id"] == "ga:1:payment_access_guard"
    assert cards[0]["apply_mode"] == "manual_review_required"
    assert cards[0]["autopilot_can_apply_now"] is False
    assert find_growth_action_card(cards, "ga:1") == cards[0]
    assert find_growth_action_card(cards, "ga:1:payment_access_guard") == cards[0]


def test_growth_action_inbox_format_is_read_only():
    cards = build_growth_action_cards([
        {
            "priority": "yellow",
            "kind": "data_quality",
            "title": "Закрыть дыры в рекламной разметке",
            "evidence": ["без расхода: 2"],
            "recommended_action": "Внести расход вручную.",
            "confidence": "high",
            "risk": "low",
        }
    ])

    inbox_text = format_growth_action_inbox(cards, period="today")
    card_text = format_growth_action_card(cards[0], period="today")

    assert "Growth Action Inbox" in inbox_text
    assert "не меняют бюджеты" in inbox_text
    assert "manual_review_required" in card_text
    assert "autopilot_can_apply_now=False" in card_text
