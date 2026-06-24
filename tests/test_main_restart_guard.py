from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest


def _load_restart_limit():
    """Load main._restart_limit without importing app.py side effects."""
    tree = ast.parse(Path("main.py").read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_restart_limit"
    )
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "os": os,
        "max": max,
        "int": int,
        "TypeError": TypeError,
        "ValueError": ValueError,
    }
    exec(compile(module, "main.py", "exec"), namespace)  # noqa: S102
    return namespace["_restart_limit"]


def test_restart_limit_defaults_to_finite_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_SELF_HEAL_MAX_RESTARTS", raising=False)

    assert _load_restart_limit()() == 3


def test_restart_limit_accepts_explicit_unlimited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SELF_HEAL_MAX_RESTARTS", "0")

    assert _load_restart_limit()() == 0


def test_restart_limit_rejects_bad_values_to_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_SELF_HEAL_MAX_RESTARTS", "bad")

    assert _load_restart_limit()() == 3
