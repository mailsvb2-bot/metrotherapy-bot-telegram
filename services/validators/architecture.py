from __future__ import annotations

import ast
import re
from pathlib import Path

from core.paths import ROOT as PROJECT_ROOT
from services.validators.base import ValidationError

SECRET_PATTERNS = (
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{25,}\b"),  # Telegram bot/provider token-like
    re.compile(r"live_[A-Za-z0-9_-]{16,}"),              # YooKassa live secret-like
)

RAW_NETWORK_IMPORTS = {
    "http.client",
    "socket",
    "urllib.error",
    "urllib.request",
}
RAW_NETWORK_CALLS = {
    ("urllib.request", "Request"),
    ("urllib.request", "urlopen"),
    ("socket", "socket"),
    ("socket", "create_connection"),
    ("http.client", "HTTPConnection"),
    ("http.client", "HTTPSConnection"),
}


def _py_files() -> list[Path]:
    skip = {"__pycache__", ".git", ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", ".venv", "venv"}
    return [p for p in PROJECT_ROOT.rglob("*.py") if not any(part in skip for part in p.parts)]


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def validate_no_embedded_secrets(*, strict: bool = True) -> None:
    bad: list[str] = []
    for p in PROJECT_ROOT.rglob("*"):
        if not p.is_file() or any(part in {".git", "__pycache__", ".pytest_cache", "dist"} for part in p.parts):
            continue
        if p.suffix.lower() in {".opus", ".ogg", ".mp3", ".wav", ".m4a", ".png", ".jpg", ".jpeg", ".zip"}:
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(rx.search(txt) for rx in SECRET_PATTERNS):
            bad.append(str(p.relative_to(PROJECT_ROOT)).replace("\\", "/"))
    if bad:
        msg = "Embedded live-looking secrets found in repository files: " + ", ".join(sorted(set(bad))[:30])
        if strict:
            raise ValidationError(msg)


def validate_single_decision_core(*, strict: bool = True) -> None:
    classes: list[str] = []
    direct_runners: list[str] = []
    for p in _py_files():
        rel = str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise ValidationError(f"Syntax error while scanning architecture: {rel}:{exc.lineno}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "DecisionCore":
                classes.append(f"{rel}:{node.lineno}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run":
                if isinstance(node.func.value, ast.Name) and "runner" in node.func.value.id.lower():
                    # Allowed at the action gateway boundary and in tests.
                    if rel not in {"core/ai/action_gateway.py", "runtime/telegram_action_runner.py"} and not rel.startswith("tests/"):
                        direct_runners.append(f"{rel}:{node.lineno}")
    if len(classes) != 1 or not classes[0].startswith("core/ai/decision_core.py:"):
        msg = "DecisionCore must have exactly one implementation. Found: " + ", ".join(classes or ["<none>"])
        if strict:
            raise ValidationError(msg)
    if direct_runners:
        msg = "Potential DecisionCore bypass: direct runner.run calls outside action gateway: " + ", ".join(direct_runners[:30])
        if strict:
            raise ValidationError(msg)


def validate_no_duplicate_fsm_states(*, strict: bool = True) -> None:
    state_classes: dict[str, list[str]] = {}
    for p in _py_files():
        rel = str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
        if rel.startswith("tests/"):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {getattr(base, "id", "") for base in node.bases}
            bases |= {getattr(base, "attr", "") for base in node.bases}
            if "StatesGroup" in bases:
                state_classes.setdefault(node.name, []).append(f"{rel}:{node.lineno}")
    duplicates = {name: where for name, where in state_classes.items() if len(where) > 1}
    if duplicates:
        msg = "Duplicate FSM state class names create aiogram split-brain: " + repr(duplicates)
        if strict:
            raise ValidationError(msg)


def validate_runtime_has_no_raw_network(*, strict: bool = True) -> None:
    """Runtime ingress must not own raw network provider calls.

    Provider HTTP clients belong in service/effects layers, so webhook runtime
    files stay thin: parse request, verify auth, dispatch to canonical services.
    """
    violations: list[str] = []
    for p in _py_files():
        rel = str(p.relative_to(PROJECT_ROOT)).replace("\\", "/")
        if not rel.startswith("runtime/") or rel.startswith("runtime/messenger_senders.py"):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            raise ValidationError(f"Syntax error while scanning runtime network policy: {rel}:{exc.lineno}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in RAW_NETWORK_IMPORTS:
                        violations.append(f"{rel}:{node.lineno}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module in RAW_NETWORK_IMPORTS:
                    violations.append(f"{rel}:{node.lineno}: from {module} import ...")
            elif isinstance(node, ast.Call):
                name = _dotted_name(node.func)
                for module, attr in RAW_NETWORK_CALLS:
                    if name == f"{module}.{attr}":
                        violations.append(f"{rel}:{node.lineno}: {name}(...)")
    if violations:
        msg = "Raw network calls/imports are forbidden in runtime ingress: " + ", ".join(violations[:30])
        if strict:
            raise ValidationError(msg)


def validate_architecture_contracts(*, strict: bool = True) -> None:
    validate_no_embedded_secrets(strict=strict)
    validate_single_decision_core(strict=strict)
    validate_no_duplicate_fsm_states(strict=strict)
    validate_runtime_has_no_raw_network(strict=strict)
