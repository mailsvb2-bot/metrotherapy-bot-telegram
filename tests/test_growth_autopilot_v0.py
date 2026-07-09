from services.growth_autopilot import diagnose_growth_snapshot, parse_ad_spend_to_minor


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
        "funnel": {"start_users": 50, "demo_sent_users": 30, "demo_ack_users": 20, "tariff_open_users": 12, "paid_users": 5},
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
