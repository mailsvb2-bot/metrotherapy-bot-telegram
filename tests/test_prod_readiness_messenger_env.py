from __future__ import annotations

import importlib


def _run(monkeypatch, **env):
    keys = {
        "APP_ENV",
        "BOT_TOKEN",
        "PAY_PROVIDER_TOKEN",
        "ADMIN_IDS",
        "ADMIN_ID",
        "HEALTHCHECK_ENABLED",
        "TELEGRAM_TRANSPORT",
        "TELEGRAM_WEBHOOK_ENABLED",
        "MESSENGER_WEBHOOK_ENABLED",
        "MESSENGER_PUBLIC_BASE_URL",
        "MAX_BOT_TOKEN",
        "MAX_BOT_LINK_BASE",
        "VK_GROUP_ID",
        "VK_GROUP_TOKEN",
        "VK_CONFIRMATION_TOKEN",
        "VK_SECRET",
    }
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    mod = importlib.import_module("scripts.prod_readiness_check")
    importlib.reload(mod)
    return mod.run()


def _base_dev_env() -> dict[str, str]:
    # Use dev mode here to test VK/MAX readiness contract without placing
    # token-shaped strings in the repository.
    return {
        "APP_ENV": "dev",
        "HEALTHCHECK_ENABLED": "1",
    }


def test_readiness_fails_when_messenger_webhook_enabled_without_vk_or_max(monkeypatch):
    errors, warnings = _run(
        monkeypatch,
        **_base_dev_env(),
        MESSENGER_WEBHOOK_ENABLED="1",
        MESSENGER_PUBLIC_BASE_URL="https://metrotherapy.ru",
    )

    assert any("VK or MAX env" in error for error in errors)


def test_readiness_fails_when_vk_env_is_partial(monkeypatch):
    errors, warnings = _run(
        monkeypatch,
        **_base_dev_env(),
        MESSENGER_WEBHOOK_ENABLED="1",
        MESSENGER_PUBLIC_BASE_URL="https://metrotherapy.ru",
        VK_GROUP_ID="238191212",
        VK_GROUP_TOKEN="vk-token-for-test",
    )

    assert any("VK_CONFIRMATION_TOKEN" in error for error in errors)


def test_readiness_accepts_complete_vk_and_max_env_for_messenger_contract(monkeypatch):
    errors, warnings = _run(
        monkeypatch,
        **_base_dev_env(),
        MESSENGER_WEBHOOK_ENABLED="1",
        MESSENGER_PUBLIC_BASE_URL="https://metrotherapy.ru",
        MAX_BOT_TOKEN="max-token-for-test",
        MAX_BOT_LINK_BASE="https://max.example/bot/{payload}",
        VK_GROUP_ID="238191212",
        VK_GROUP_TOKEN="vk-token-for-test",
        VK_CONFIRMATION_TOKEN="confirm-token-for-test",
        VK_SECRET="vk-secret-for-test",
    )

    assert not any("MESSENGER" in error or "VK_" in error or "MAX_" in error for error in errors)
    assert not any("VK_SECRET" in warning for warning in warnings)


def test_readiness_warns_when_vk_secret_absent(monkeypatch):
    errors, warnings = _run(
        monkeypatch,
        **_base_dev_env(),
        MESSENGER_WEBHOOK_ENABLED="1",
        MESSENGER_PUBLIC_BASE_URL="https://metrotherapy.ru",
        VK_GROUP_ID="238191212",
        VK_GROUP_TOKEN="vk-token-for-test",
        VK_CONFIRMATION_TOKEN="confirm-token-for-test",
    )

    assert any("VK_SECRET" in warning for warning in warnings)
