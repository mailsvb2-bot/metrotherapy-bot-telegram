from __future__ import annotations

from services import growth_click_tracking


def test_click_redirect_target_points_to_telegram_start(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "metrotherapybot")

    target = growth_click_tracking.build_click_redirect_target("src_telegram_ads__camp_may")

    assert target == "https://t.me/metrotherapybot?start=src_telegram_ads__camp_may"


def test_record_click_redirect_logs_sanitized_non_user_event(monkeypatch):
    captured = {}

    def fake_log_runtime_event(user_id, *, event_type, payload, source, correlation_id=None, decision_id=None, conn=None):
        captured.update({
            "user_id": user_id,
            "event_type": event_type,
            "payload": payload,
            "source": source,
            "correlation_id": correlation_id,
            "decision_id": decision_id,
            "conn": conn,
        })

    monkeypatch.setattr(growth_click_tracking, "log_runtime_event", fake_log_runtime_event)

    meta = growth_click_tracking.record_click_redirect(
        "src_telegram_ads__camp_may__creative_reels1\n",
        request_meta={"user_agent": "pytest", "referer": "https://example.test", "ip": "127.0.0.1"},
    )

    assert captured["user_id"] == 0
    assert captured["event_type"] == "ad_click_redirect"
    assert captured["source"] == "growth_redirect"
    assert captured["payload"]["payload"] == "src_telegram_ads__camp_may__creative_reels1"
    assert captured["payload"]["source"] == "telegram_ads"
    assert captured["payload"]["campaign"] == "may"
    assert captured["payload"]["creative"] == "reels1"
    assert captured["payload"]["user_agent"] == "pytest"
    assert "ip" not in captured["payload"]
    assert meta == captured["payload"]
