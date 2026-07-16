from services.messenger import preflight


def test_delivery_preflight_is_green_before_runtime_start(monkeypatch):
    monkeypatch.setattr(preflight, "vk_webhook_enabled", lambda: True)
    monkeypatch.setattr(preflight, "max_webhook_enabled", lambda: False)
    monkeypatch.setattr(
        preflight,
        "delivery_health_snapshot",
        lambda: {
            "worker_expected": False,
            "worker_active": False,
            "worker_running": True,
            "pending": 0,
            "retry": 0,
            "sending": 0,
            "sent": 0,
            "dead": 0,
        },
    )

    status = preflight.check_delivery_outbox_preflight()
    assert status.ok is True
    assert status.missing == ()


def test_delivery_preflight_fails_when_expected_worker_stops(monkeypatch):
    monkeypatch.setattr(preflight, "vk_webhook_enabled", lambda: True)
    monkeypatch.setattr(preflight, "max_webhook_enabled", lambda: False)
    monkeypatch.setattr(
        preflight,
        "delivery_health_snapshot",
        lambda: {
            "worker_expected": True,
            "worker_active": False,
            "worker_running": False,
            "pending": 2,
            "retry": 0,
            "sending": 0,
            "sent": 10,
            "dead": 0,
        },
    )

    status = preflight.check_delivery_outbox_preflight()
    assert status.ok is False
    assert "delivery_worker(running)" in status.missing


def test_delivery_preflight_fails_closed_on_dead_letters(monkeypatch):
    monkeypatch.setattr(preflight, "vk_webhook_enabled", lambda: False)
    monkeypatch.setattr(preflight, "max_webhook_enabled", lambda: True)
    monkeypatch.setattr(
        preflight,
        "delivery_health_snapshot",
        lambda: {
            "worker_expected": True,
            "worker_active": True,
            "worker_running": True,
            "pending": 0,
            "retry": 3,
            "sending": 0,
            "sent": 10,
            "dead": 1,
        },
    )

    status = preflight.check_delivery_outbox_preflight()
    assert status.ok is False
    assert "dead_letters=1" in status.missing
    assert "delivery retries pending: 3" in status.warnings
