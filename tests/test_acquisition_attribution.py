from __future__ import annotations

from services import admin_ad_links
from services.acquisition_attribution import start_attribution_meta


def test_start_attribution_meta_parses_query_payload():
    meta = start_attribution_meta(
        "utm_source=telegram_ads&utm_campaign=may_launch&utm_creative=video_1&ad_spend=340rub"
    )

    assert meta["payload"].startswith("utm_source=telegram_ads")
    assert meta["utm_source"] == "telegram_ads"
    assert meta["source"] == "telegram_ads"
    assert meta["utm_campaign"] == "may_launch"
    assert meta["campaign"] == "may_launch"
    assert meta["utm_creative"] == "video_1"
    assert meta["creative"] == "video_1"
    assert meta["ad_spend"] == "340rub"


def test_start_attribution_meta_parses_telegram_safe_tokens():
    meta = start_attribution_meta("src_telegram_ads__camp_may__creative_reels1__cost_340rub")

    assert meta["source"] == "telegram_ads"
    assert meta["campaign"] == "may"
    assert meta["creative"] == "reels1"
    assert meta["ad_spend"] == "340rub"


def test_start_attribution_meta_resolves_short_ad_payload(monkeypatch):
    monkeypatch.setattr(
        admin_ad_links,
        "resolve_ad_link_payload",
        lambda payload: {
            "utm_source": "telegram_ads",
            "utm_campaign": "july",
            "utm_creative": "reels7",
            "ad_spend": "900rub",
        }
        if payload == "ad_17"
        else None,
    )

    meta = start_attribution_meta("ad_17")

    assert meta["payload"] == "ad_17"
    assert meta["source"] == "telegram_ads"
    assert meta["campaign"] == "july"
    assert meta["creative"] == "reels7"
    assert meta["ad_spend"] == "900rub"


def test_start_attribution_meta_ignores_unknown_keys():
    meta = start_attribution_meta("utm_source=telegram_ads&password=secret&token=hidden")

    assert meta["source"] == "telegram_ads"
    assert "password" not in meta
    assert "token" not in meta
