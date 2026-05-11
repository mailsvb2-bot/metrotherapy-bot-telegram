from __future__ import annotations

import importlib


def _run(monkeypatch, **env):
    keys = {
        "APP_ENV",
        "BOT_TOKEN",
        "PAY_PROVIDER_TOKEN",
        "ADMIN_IDS",
        "TELEGRAM_TRANSPORT",
        "RUN_MODE",
        "TELEGRAM_WEBHOOK_ENABLED",
        "MESSENGER_WEBHOOK_ENABLED",
        "MESSENGER_WEBHOOK_HOST",
        "MESSENGER_WEBHOOK_PORT",
        "MESSENGER_PUBLIC_BASE_URL",
        "HEALTHCHECK_ENABLED",
        "HEALTHCHECK_HOST",
        "HEALTHCHECK_PORT",
        "METRO_DB_PATH",
        "LOG_PATH",
    }
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    mod = importlib.import_module("scripts.runtime_contract")
    importlib.reload(mod)
    return mod.run()


def _prod_base(tmp_path) -> dict[str, str]:
    return {
        "APP_ENV": "prod",
        "BOT_TOKEN": "validation-bot-token",
        "PAY_PROVIDER_TOKEN": "validation-pay-token",
        "ADMIN_IDS": "1",
        "TELEGRAM_TRANSPORT": "polling",
        "TELEGRAM_WEBHOOK_ENABLED": "0",
        "HEALTHCHECK_ENABLED": "1",
        "METRO_DB_PATH": str(tmp_path / "state" / "data.db"),
        "LOG_PATH": str(tmp_path / "logs" / "app.log"),
    }


def test_runtime_contract_accepts_prod_polling_with_out_of_tree_state(monkeypatch, tmp_path):
    errors, warnings = _run(monkeypatch, **_prod_base(tmp_path))

    assert errors == []


def test_runtime_contract_rejects_telegram_webhook(monkeypatch, tmp_path):
    env = _prod_base(tmp_path)
    env["TELEGRAM_WEBHOOK_ENABLED"] = "1"

    errors, warnings = _run(monkeypatch, **env)

    assert any("Telegram" in error or "TELEGRAM" in error for error in errors)


def test_runtime_contract_rejects_repo_relative_state_paths(monkeypatch):
    env = {
        "APP_ENV": "prod",
        "BOT_TOKEN": "validation-bot-token",
        "PAY_PROVIDER_TOKEN": "validation-pay-token",
        "ADMIN_IDS": "1",
        "TELEGRAM_TRANSPORT": "polling",
        "TELEGRAM_WEBHOOK_ENABLED": "0",
        "HEALTHCHECK_ENABLED": "1",
        "METRO_DB_PATH": "data/data.db",
        "LOG_PATH": "logs/app.log",
    }

    errors, warnings = _run(monkeypatch, **env)

    assert any("METRO_DB_PATH" in error for error in errors)
    assert any("LOG_PATH" in error for error in errors)


def test_runtime_contract_detects_messenger_health_port_collision(monkeypatch, tmp_path):
    env = _prod_base(tmp_path)
    env.update(
        {
            "MESSENGER_WEBHOOK_ENABLED": "1",
            "MESSENGER_WEBHOOK_HOST": "127.0.0.1",
            "MESSENGER_WEBHOOK_PORT": "8082",
            "MESSENGER_PUBLIC_BASE_URL": "https://example.invalid",
            "HEALTHCHECK_HOST": "127.0.0.1",
            "HEALTHCHECK_PORT": "8082",
        }
    )

    errors, warnings = _run(monkeypatch, **env)

    assert any("collide" in error for error in errors)
