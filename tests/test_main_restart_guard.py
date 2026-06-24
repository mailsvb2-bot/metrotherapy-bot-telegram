from __future__ import annotations

import pytest

import main


def test_restart_limit_defaults_to_finite_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_SELF_HEAL_MAX_RESTARTS", raising=False)

    assert main._restart_limit() == 3


def test_restart_limit_accepts_explicit_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SELF_HEAL_MAX_RESTARTS", "0")

    assert main._restart_limit() == 0


def test_restart_limit_rejects_bad_values_to_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SELF_HEAL_MAX_RESTARTS", "bad")

    assert main._restart_limit() == 3
