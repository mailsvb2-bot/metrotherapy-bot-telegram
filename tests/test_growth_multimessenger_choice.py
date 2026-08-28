from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp import web

from runtime import health_server, messenger_ingress, messenger_webhooks
from services import growth_click_tracking
from services.growth_creative_diagnostics import build_creative_diagnostics, format_creative_diagnostics
from services.messenger import links as messenger_links


@pytest.fixture
def messenger_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(messenger_links.settings, "TELEGRAM_BOT_USERNAME", "metrotherapybot", raising=False)
    monkeypatch.setattr(messenger_links.settings, "MAX_BOT_NAME", "metrotherapy", raising=False)
    monkeypatch.setattr(messenger_links.settings, "MAX_BOT_LINK_BASE", "https://max.ru/metrotherapy", raising=False)
    monkeypatch.setattr(messenger_links.settings, "VK_GROUP_ID", "123456", raising=False)


def test_choice_targets_preserve_attribution_for_all_messengers(messenger_settings: None) -> None:
    payload = "src_partner__camp_launch__creative_email"
    targets = growth_click_tracking.build_choice_targets(payload)
    assert [item["platform"] for item in targets] == ["telegram", "vk", "max"]
    by_platform = {item["platform"]: item["url"] for item in targets}
    assert by_platform["telegram"] == f"https://t.me/metrotherapybot?start={payload}"
    assert by_platform["vk"] == f"https://vk.com/im?sel=-123456&start={payload}"
    assert by_platform["max"] == f"https://max.ru/metrotherapy?start={payload}"
    assert growth_click_tracking.build_platform_redirect_target(payload, "vk") == by_platform["vk"]
    assert growth_click_tracking.build_platform_redirect_target(payload, "unknown") == ""


def test_choice_page_uses_internal_tracking_routes(messenger_settings: None) -> None:
    payload = "src_partner__camp_launch__creative_email"
    html = growth_click_tracking.render_messenger_choice_html(payload)
    assert "Где вам удобнее проходить аудиопрактики?" in html
    assert "/pay/r/src_partner__camp_launch__creative_email/telegram" in html
    assert "/pay/r/src_partner__camp_launch__creative_email/vk" in html
    assert "/pay/r/src_partner__camp_launch__creative_email/max" in html
    assert html.index("Telegram") < html.index("ВКонтакте") < html.index("MAX")


def test_growth_events_track_landing_and_messenger_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict[str, object]] = []

    def fake_log(user_id: int, *, event_type: str, payload: dict, source: str, **_kwargs: object) -> None:
        captured.append({"user_id": user_id, "event_type": event_type, "payload": payload, "source": source})
    monkeypatch.setattr(growth_click_tracking, "log_runtime_event", fake_log)
    payload = "src_partner__camp_launch__creative_email"
    landing = growth_click_tracking.record_choice_landing(payload)
    choice = growth_click_tracking.record_messenger_choice(payload, platform="vk")

    assert landing["redirect_target"] == "messenger_choice"
    assert choice["messenger"] == "vk"
    assert [item["event_type"] for item in captured] == ["ad_click_redirect", "messenger_choice"]
    assert captured[0]["payload"]["source"] == "partner"
    assert captured[1]["payload"]["campaign"] == "launch"


@pytest.mark.asyncio
async def test_growth_choice_http_handlers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(health_server, "render_messenger_choice_html", lambda payload: f"<html>{payload}</html>")
    monkeypatch.setattr(
        health_server,
        "record_choice_landing",
        lambda payload, request_meta: calls.append(("landing", payload)),
    )
    request = SimpleNamespace(
        method="GET",
        match_info={"payload": "src_partner__camp_x"},
        headers={"User-Agent": "pytest", "Referer": "ref"},
    )
    response = await health_server.growth_choice_landing(request)
    assert response.status == 200
    assert "src_partner__camp_x" in response.text
    assert calls == [("landing", "src_partner__camp_x")]

    monkeypatch.setattr(
        health_server,
        "build_platform_redirect_target",
        lambda payload, platform: f"https://example/{platform}?start={payload}" if platform in {"telegram", "vk", "max"} else "",
    )
    monkeypatch.setattr(
        health_server,
        "record_messenger_choice",
        lambda payload, platform, request_meta: calls.append((platform, payload)),
    )
    request.match_info = {"payload": "src_partner__camp_x", "platform": "max"}
    redirect = await health_server.growth_platform_redirect(request)
    assert redirect.location == "https://example/max?start=src_partner__camp_x"
    assert calls[-1] == ("max", "src_partner__camp_x")

    request.match_info = {"payload": "src_partner__camp_x", "platform": "bad"}
    with pytest.raises(web.HTTPNotFound):
        await health_server.growth_platform_redirect(request)


def test_acquisition_payloads_become_common_start_entrypoints(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = [
        "site",
        "ad_12",
        "src_partner__camp_launch__creative_email",
        "utm_source=partner&utm_campaign=launch",
    ]
    for token in tokens:
        assert messenger_ingress._entry_start_text(token) == f"/start {token}"
    assert messenger_ingress._entry_start_text("ad_sale") == "ad_sale"

    events: list[tuple[int, str, dict]] = []
    monkeypatch.setattr(
        messenger_ingress,
        "log_runtime_event",
        lambda uid, *, event_type, payload, source: events.append((uid, event_type, payload)),
    )
    messenger_ingress._log_acquisition_start_if_needed(
        77,
        platform="vk",
        normalized_text="/start src_partner__camp_launch__creative_email",
    )
    assert events[0][0:2] == (77, "funnel_start_command")
    assert events[0][2]["source"] == "partner"
    assert events[0][2]["campaign"] == "launch"
    assert events[0][2]["platform"] == "vk"


def test_messenger_choice_is_visible_in_creative_diagnostics() -> None:
    meta = {"source": "partner", "campaign": "launch", "creative": "email", "messenger": "vk"}
    summary = build_creative_diagnostics(
        ad_links={"latest": []},
        event_rows=[
            {"name": "ad_click_redirect", "meta": json.dumps(meta)},
            {"name": "messenger_choice", "meta": json.dumps(meta)},
            {"name": "messenger_choice", "meta": json.dumps({**meta, "messenger": "max"})},
        ],
    )
    item = summary["items"][0]
    assert item["clicks"] == 1
    assert item["messenger_choices"] == 2
    assert item["messenger_breakdown"] == {"vk": 1, "max": 1}
    report = "\n".join(format_creative_diagnostics(summary))
    assert "выбор мессенджера 2" in report
    assert "VK 1" in report
    assert "MAX 1" in report


def test_public_growth_routes_include_choice_and_platform_redirect() -> None:
    class Router:
        def __init__(self) -> None:
            self.paths: list[tuple[str, object]] = []

        def add_get(self, path: str, handler: object) -> None:
            self.paths.append((path, handler))

    app = SimpleNamespace(router=Router())
    messenger_webhooks._register_growth_routes(app)
    paths = [path for path, _handler in app.router.paths]
    assert "/a/{payload}" in paths
    assert "/pay/r/{payload}" in paths
    assert "/pay/r/{payload}/{platform}" in paths
