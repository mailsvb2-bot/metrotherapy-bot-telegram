from __future__ import annotations

import json

from services.growth_creative_diagnostics import build_creative_diagnostics, format_creative_diagnostics


def test_creative_diagnostics_aggregates_links_and_events():
    ad_links = {
        "latest": [
            {
                "source": "telegram_ads",
                "campaign": "may",
                "creative": "reels1",
                "ad_spend": "340rub",
            }
        ]
    }
    meta = {"source": "telegram_ads", "campaign": "may", "creative": "reels1"}
    events = [
        {"name": "ad_click_redirect", "meta": json.dumps(meta)},
        {"name": "ad_click_redirect", "meta": json.dumps(meta)},
        {"name": "funnel_start_command", "meta": json.dumps(meta)},
        {"name": "demo_ack", "meta": json.dumps(meta)},
        {"name": "payment_success", "meta": json.dumps(meta)},
    ]

    summary = build_creative_diagnostics(ad_links=ad_links, event_rows=events)

    item = summary["items"][0]
    assert summary["tracked_creatives"] == 1
    assert item["links"] == 1
    assert item["spend_minor_low_confidence"] == 34000
    assert item["clicks"] == 2
    assert item["starts"] == 1
    assert item["demo_ack"] == 1
    assert item["payments"] == 1
    assert item["click_to_start_pct"] == 50.0
    assert item["click_to_payment_pct"] == 50.0
    assert item["cost_per_click_minor_low_confidence"] == 17000
    assert item["cost_per_payment_minor_low_confidence"] == 34000


def test_creative_diagnostics_counts_unattributed_events_without_crashing():
    summary = build_creative_diagnostics(
        ad_links={"latest": []},
        event_rows=[
            {"name": "ad_click_redirect", "meta": "not-json"},
            {"name": "funnel_start_command", "meta": "{}"},
        ],
    )

    assert summary["items"] == []
    assert summary["unattributed_events"] == 2


def test_creative_diagnostics_format_is_plain_read_only_report():
    summary = build_creative_diagnostics(
        ad_links={"latest": [{"source": "partner", "campaign": "launch", "creative": "post1", "ad_spend": "100rub"}]},
        event_rows=[{"name": "ad_click_redirect", "meta": json.dumps({"source": "partner", "campaign": "launch", "creative": "post1"})}],
    )

    lines = format_creative_diagnostics(summary)
    text = "\n".join(lines)

    assert "Креативы / кампании" in text
    assert "partner / launch / post1" in text
    assert "CPC" in text
