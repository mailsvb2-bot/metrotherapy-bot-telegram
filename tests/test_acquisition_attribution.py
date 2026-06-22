from __future__ import annotations

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


def test_start_attribution_meta_ignores_unknown_keys():
    meta = start_attribution_meta("utm_source=telegram_ads&password=secret&token=hidden")

    assert meta["source"] == "telegram_ads"
    assert "password" not in meta
    assert "token" not in meta
