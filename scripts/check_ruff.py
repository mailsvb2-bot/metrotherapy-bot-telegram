from __future__ import annotations

import os
# Reviewed: operator-only quality gate invokes the local Python Ruff module with fixed arguments.
import subprocess  # nosec B404
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUFF_TARGETS = (
    "services",
    "scripts",
    "handlers",
    "core",
    "runtime",
    "config",
    "tests",
    "app.py",
    "main.py",
)
VENV_PREFIXES = (".venv", "venv", "env")


def _existing_project_targets() -> list[str]:
    targets: list[str] = []
    for target in RUFF_TARGETS:
        path = ROOT / target
        if path.exists():
            targets.append(target)
    return targets


def main() -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    targets = _existing_project_targets()
    if not targets:
        print("No project targets found for Ruff quality gate")
        return 2
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        *targets,
        "--exclude",
        ".venv*",
        "--exclude",
        "venv*",
        "--exclude",
        "env*",
    ]
    print("== Ruff quality gate ==")
    print("cwd:", ROOT)
    print("cmd:", " ".join(cmd))
    # Reviewed: fixed local lint command, no shell, project target allow-list only.
    return subprocess.call(cmd, cwd=ROOT, env=env)  # nosec B603


if __name__ == "__main__":
    raise SystemExit(main())
