from __future__ import annotations

"""One-command non-bypassable regression contour for CI and local release checks."""

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from services.command_runner import run_command

ROOT = Path(__file__).resolve().parents[1]
PROJECT_SURFACE = (
    "services",
    "scripts",
    "handlers",
    "core",
    "runtime",
    "config",
    "app.py",
    "main.py",
)
GENERATED_PYTHON_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
GENERATED_FILE_SUFFIXES = {".pyc", ".pyo"}
VIRTUALENV_DIR_NAMES = {".venv", "venv", "env"}
VIRTUALENV_DIR_PREFIXES = (".venv-", "venv-", "env-")
SKIP_CLEANUP_DIRS = {".git", *VIRTUALENV_DIR_NAMES}


@dataclass(frozen=True)
class GateStep:
    name: str
    cmd: tuple[str, ...]
    env: dict[str, str] | None = None


BASE_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "LOAD_DOTENV": "0",
    "TELEGRAM_TRANSPORT": "polling",
    "TELEGRAM_WEBHOOK_ENABLED": "0",
    "TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED": "0",
}

STRICT_VALIDATOR_ENV = {
    "APP_ENV": "test",
    "VALIDATOR_RELEASE_MODE": "1",
    "VALIDATOR_GUARDRAILS_STRICT": "1",
    "VALIDATOR_SKIP_AUDIO": "1",
}

PYTEST_ENV = {
    "APP_ENV": "test",
    "LOAD_DOTENV": "0",
}

STEPS = (
    GateStep(
        "release hygiene before checks",
        (sys.executable, "scripts/check_release_hygiene.py"),
    ),
    GateStep(
        "compile project surface",
        (sys.executable, "-m", "compileall", *PROJECT_SURFACE),
    ),
    GateStep(
        "hermetic smoke no polling",
        (sys.executable, "scripts/smoke.py"),
        STRICT_VALIDATOR_ENV,
    ),
    GateStep(
        "full pytest regression gate",
        (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"),
        PYTEST_ENV,
    ),
    GateStep(
        "strict validation",
        (sys.executable, "scripts/validate_project.py"),
        STRICT_VALIDATOR_ENV,
    ),
    GateStep(
        "ruff quality gate",
        (sys.executable, "scripts/check_ruff.py"),
    ),
    GateStep(
        "release hygiene after checks",
        (sys.executable, "scripts/check_release_hygiene.py"),
    ),
)


def _is_local_virtualenv_dir(dirname: str) -> bool:
    return dirname in VIRTUALENV_DIR_NAMES or dirname.startswith(VIRTUALENV_DIR_PREFIXES)


def _cleanup_generated_python_artifacts() -> None:
    """Remove artifacts created by local focused tests, compileall and pytest.

    The release hygiene gate must still catch real shippable garbage such as DBs,
    logs or build fragments. Python bytecode and test/linter caches are deterministic
    local by-products of the gate itself, so the contour owns their cleanup.
    """
    for current, dirs, files in os.walk(ROOT):
        current_path = Path(current)
        dirs[:] = [name for name in dirs if name not in SKIP_CLEANUP_DIRS and not _is_local_virtualenv_dir(name)]
        for dirname in list(dirs):
            if dirname in GENERATED_PYTHON_DIRS:
                shutil.rmtree(current_path / dirname, ignore_errors=True)
                dirs.remove(dirname)
        for filename in files:
            if Path(filename).suffix in GENERATED_FILE_SUFFIXES:
                try:
                    (current_path / filename).unlink()
                except FileNotFoundError:
                    pass


def _run(step: GateStep) -> int:
    if step.name.startswith("release hygiene"):
        _cleanup_generated_python_artifacts()

    env = os.environ.copy()
    env.update(BASE_ENV)
    if step.env:
        env.update(step.env)

    print(f"==> {step.name}", flush=True)
    print("cmd:", " ".join(step.cmd), flush=True)
    completed = run_command(step.cmd, cwd=ROOT, env=env, check=False)
    if completed.returncode != 0:
        print(f"REGRESSION_GATE_FAILED step={step.name!r} code={completed.returncode}", flush=True)
        return int(completed.returncode)
    return 0


def main() -> int:
    for step in STEPS:
        code = _run(step)
        if code:
            return code
    print("REGRESSION_GATE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
