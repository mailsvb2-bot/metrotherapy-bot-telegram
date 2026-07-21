from __future__ import annotations

from dataclasses import dataclass

import pytest

from config import prod_contract
from core.runtime import self_healing


PROD_ENV_NAMES = (
    "APP_ENV",
    "TELEGRAM_TRANSPORT",
    "RUN_MODE",
    "TELEGRAM_WEBHOOK_ENABLED",
    "TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED",
    "ALLOW_INSECURE_TELEGRAM_WEBHOOK",
    "METRO_DB_ENGINE",
    "DATABASE_URL",
    "ALLOW_SQLITE_IN_PROD",
)


def clear_prod_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PROD_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def set_valid_prod_env(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_prod_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "polling")
    monkeypatch.setenv("METRO_DB_ENGINE", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db/app")


def test_prod_contract_env_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_prod_env(monkeypatch)
    assert prod_contract._env("APP_ENV") == ""

    monkeypatch.setenv("APP_ENV", "  PROD  ")
    assert prod_contract._env("APP_ENV") == "PROD"

    for raw in ("1", "true", "YES", "on", "webhook"):
        monkeypatch.setenv("TELEGRAM_WEBHOOK_ENABLED", raw)
        assert prod_contract._truthy("TELEGRAM_WEBHOOK_ENABLED") is True
    monkeypatch.setenv("TELEGRAM_WEBHOOK_ENABLED", "0")
    assert prod_contract._truthy("TELEGRAM_WEBHOOK_ENABLED") is False
    monkeypatch.delenv("TELEGRAM_WEBHOOK_ENABLED", raising=False)
    assert prod_contract._truthy("TELEGRAM_WEBHOOK_ENABLED", "1") is True


def test_db_engine_aliases_and_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_prod_env(monkeypatch)
    for raw in ("postgres", "postgresql", "pg"):
        monkeypatch.setenv("METRO_DB_ENGINE", raw)
        assert prod_contract._db_engine() == "postgres"
    for raw in ("sqlite", "sqlite3"):
        monkeypatch.setenv("METRO_DB_ENGINE", raw)
        assert prod_contract._db_engine() == "sqlite"

    monkeypatch.delenv("METRO_DB_ENGINE", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://db")
    assert prod_contract._db_engine() == "postgres"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert prod_contract._db_engine() == "sqlite"


def test_non_prod_and_valid_prod_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_prod_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "stage")
    prod_contract.validate_production_contract()

    set_valid_prod_env(monkeypatch)
    prod_contract.validate_production_contract()

    monkeypatch.setenv("DATABASE_URL", "POSTGRES://db/app")
    prod_contract.validate_production_contract()


def test_prod_contract_aggregates_all_violations(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_prod_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("TELEGRAM_TRANSPORT", "webhook")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED", "true")
    monkeypatch.setenv("ALLOW_INSECURE_TELEGRAM_WEBHOOK", "yes")
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")
    monkeypatch.setenv("ALLOW_SQLITE_IN_PROD", "on")

    with pytest.raises(prod_contract.ProductionContractError) as exc_info:
        prod_contract.validate_production_contract()

    message = str(exc_info.value)
    assert "TELEGRAM_TRANSPORT must be polling" in message
    assert "TELEGRAM_WEBHOOK_ENABLED must be 0" in message
    assert "TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED must be 0" in message
    assert "ALLOW_INSECURE_TELEGRAM_WEBHOOK is forbidden" in message
    assert "METRO_DB_ENGINE must be postgres" in message
    assert "DATABASE_URL is required" in message
    assert "ALLOW_SQLITE_IN_PROD is not a supported" in message


def test_prod_contract_rejects_non_postgres_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    set_valid_prod_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "mysql://db/app")

    with pytest.raises(prod_contract.ProductionContractError, match="postgres/postgresql scheme"):
        prod_contract.validate_production_contract()


@dataclass
class FakeSafeMode:
    active: bool = False
    reason: str = ""
    since_ts: float = 0.0
    clear_calls: int = 0

    def clear(self) -> None:
        self.clear_calls += 1
        self.active = False
        self.reason = ""
        self.since_ts = 0.0


def install_safe_mode(monkeypatch: pytest.MonkeyPatch, state: FakeSafeMode) -> None:
    monkeypatch.setattr(self_healing, "SAFE_MODE", state)


def test_self_healing_throttle_and_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    state = FakeSafeMode(active=True, reason="INVALID_TOKEN", since_ts=0)
    install_safe_mode(monkeypatch, state)
    engine = self_healing.SelfHealingEngine(cooldown_sec=10, _last_tick=100)
    monkeypatch.setattr(self_healing.time, "time", lambda: 103)
    engine.tick()
    assert engine._last_tick == 100
    assert state.clear_calls == 0

    state.active = False
    monkeypatch.setattr(self_healing.time, "time", lambda: 110)
    engine.tick()
    assert engine._last_tick == 110
    assert state.clear_calls == 0


def test_self_healing_never_clears_arch_violation(monkeypatch: pytest.MonkeyPatch) -> None:
    state = FakeSafeMode(active=True, reason="critical ARCH_VIOLATION detected", since_ts=1)
    install_safe_mode(monkeypatch, state)
    engine = self_healing.SelfHealingEngine(cooldown_sec=10)
    monkeypatch.setattr(self_healing.time, "time", lambda: 100)

    engine.tick()

    assert state.clear_calls == 0
    assert state.active is True


def test_self_healing_waits_for_cooldown_and_requires_since_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    state = FakeSafeMode(active=True, reason="BYPASS", since_ts=95)
    install_safe_mode(monkeypatch, state)
    engine = self_healing.SelfHealingEngine(cooldown_sec=10)
    monkeypatch.setattr(self_healing.time, "time", lambda: 100)
    engine.tick()
    assert state.clear_calls == 0

    state.since_ts = 0
    engine._last_tick = 0
    monkeypatch.setattr(self_healing.time, "time", lambda: 200)
    engine.tick()
    assert state.clear_calls == 0


def test_self_healing_clears_recoverable_safe_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    state = FakeSafeMode(active=True, reason="invalid_token", since_ts=80)
    install_safe_mode(monkeypatch, state)
    engine = self_healing.SelfHealingEngine(cooldown_sec=10)
    monkeypatch.setattr(self_healing.time, "time", lambda: 100)

    engine.tick()

    assert state.clear_calls == 1
    assert state.active is False
    assert engine._last_tick == 100
