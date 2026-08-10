from __future__ import annotations

from runtime import health_server


def _healthy_runtime(monkeypatch) -> None:
    monkeypatch.setattr(health_server, "_db_ready", lambda: (True, None))
    monkeypatch.setattr(health_server, "_schema_ready", lambda: (True, None))
    monkeypatch.setattr(health_server, "_scheduler_snapshot", lambda: {"scheduler_loop_task_running": True})
    monkeypatch.setattr(
        health_server,
        "_scheduler_readiness",
        lambda _snapshot: (True, [], {"scheduler_degraded": False}),
    )
    monkeypatch.setattr(health_server, "_messenger_preflight_readiness", lambda: (True, [], {}))
    monkeypatch.setattr(health_server, "_audio_ready", lambda _app_env: (True, None))
    monkeypatch.setattr(health_server, "_telegram_transport", lambda: "polling")
    monkeypatch.setattr(health_server, "_messenger_webhook_configured", lambda: False)
    monkeypatch.setattr(health_server, "_webhook_configured", lambda: False)
    monkeypatch.setattr(health_server, "http_ingress_enabled", lambda: False)
    monkeypatch.setattr(health_server, "_ingress_health_fields", lambda: {})
    monkeypatch.setattr(health_server, "_storage_health_fields", lambda: {})
    monkeypatch.setattr(health_server, "ai_policy_snapshot", lambda: {})
    monkeypatch.setattr(health_server, "required_readiness_tables", lambda: [])


def test_disabled_visual_creative_does_not_gate_readiness(monkeypatch):
    _healthy_runtime(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(
        health_server,
        "visual_creative_configuration_snapshot",
        lambda *, app_env: {
            "visual_creative_enabled": False,
            "visual_creative_ready": True,
            "visual_creative_configuration_errors": [],
        },
    )

    payload, status = health_server.build_readiness_payload()

    assert status == 200
    assert payload["ok"] is True
    assert payload["visual_creative_enabled"] is False
    assert payload["visual_creative_ready"] is True


def test_enabled_broken_visual_creative_configuration_gates_readiness(monkeypatch):
    _healthy_runtime(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setattr(
        health_server,
        "visual_creative_configuration_snapshot",
        lambda *, app_env: {
            "visual_creative_enabled": True,
            "visual_creative_ready": False,
            "visual_creative_gateway_configured": True,
            "visual_creative_gateway_token_configured": False,
            "visual_creative_configuration_errors": ["gateway_token", "deployment_country"],
        },
    )

    payload, status = health_server.build_readiness_payload()

    assert status == 500
    assert payload["ok"] is False
    assert payload["visual_creative_ready"] is False
    assert "visual_creative:gateway_token" in payload["error"]
    assert "visual_creative:deployment_country" in payload["error"]


def test_health_diagnostics_include_visual_creative_state_without_gating_health(monkeypatch):
    _healthy_runtime(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(
        health_server,
        "visual_creative_configuration_snapshot",
        lambda *, app_env: {
            "visual_creative_enabled": True,
            "visual_creative_ready": False,
            "visual_creative_configuration_errors": ["gateway_url"],
        },
    )

    payload, status = health_server.build_health_payload()

    assert status == 200
    assert payload["ok"] is True
    assert payload["visual_creative_enabled"] is True
    assert payload["visual_creative_ready"] is False
