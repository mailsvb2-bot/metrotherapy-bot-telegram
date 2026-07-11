from __future__ import annotations

import pytest

from config import settings as settings_module


def test_env_int_reports_variable_name_for_invalid_value(monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_PORT", "8082x")

    with pytest.raises(settings_module.ConfigurationError, match="HEALTHCHECK_PORT must be an integer"):
        settings_module._env_int("HEALTHCHECK_PORT", 8082, minimum=1, maximum=65535)


def test_env_int_enforces_bounds(monkeypatch):
    monkeypatch.setenv("TEST_PORT", "70000")

    with pytest.raises(settings_module.ConfigurationError, match="TEST_PORT must be <= 65535"):
        settings_module._env_int("TEST_PORT", 8081, minimum=1, maximum=65535)


def test_env_int_fallback_does_not_parse_bad_fallback_when_primary_is_set(monkeypatch):
    monkeypatch.setenv("PRIMARY_PORT", "8081")
    monkeypatch.setenv("LEGACY_PORT", "bad")

    assert settings_module._env_int_fallback(
        "PRIMARY_PORT",
        "LEGACY_PORT",
        8000,
        minimum=1,
        maximum=65535,
    ) == 8081


def test_env_int_fallback_uses_fallback_when_primary_missing(monkeypatch):
    monkeypatch.delenv("PRIMARY_PORT", raising=False)
    monkeypatch.setenv("LEGACY_PORT", "9090")

    assert settings_module._env_int_fallback(
        "PRIMARY_PORT",
        "LEGACY_PORT",
        8000,
        minimum=1,
        maximum=65535,
    ) == 9090
