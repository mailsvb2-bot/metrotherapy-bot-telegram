"""Fast guardrail for git/pre-commit/CI.

Fails if the working tree contains artifacts that must never be shipped:
- __pycache__ directories
- *.pyc/*.pyo files
- runtime SQLite DB files (data.db, data/data.db)
- test/lint caches or runtime logs
- suspicious temporary root-level packaging fragments

The guard intentionally ignores local execution metadata such as .git and
virtual environments. Those paths can exist in CI/worktrees while still being
excluded from the release artifact by packaging rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

FORBIDDEN_DB = {
    Path("data.db"),
    Path("data") / "data.db",
}
FORBIDDEN_DB_GLOBS = ["*.sqlite", "*.db-wal", "*.db-shm", "*.db-journal"]

FORBIDDEN_DIRS = {
    Path(".pytest_cache"),
    Path(".mypy_cache"),
    Path(".idea"),
    Path(".vscode"),
}

IGNORED_ROOT_NAMES = {
    ".git",
    ".venv",
    "venv",
    "env",
    ".envrc",
    ".ruff_cache",
}

FORBIDDEN_LOG_DIR = Path("logs")
FORBIDDEN_LOG_GLOBS = ["*.log"]

ALLOWED_ROOT_FILES = {
    ".env.example",
    ".gitignore",
    ".pre-commit-config.yaml",
    "README.md",
    "VERSION",
    "SOVEREIGNTY_BUILD_MANIFEST.json",
    "app.py",
    "main.py",
    "check_db.py",
    "pyproject.toml",
    "requirements.in",
    "requirements-dev.in",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-py313.txt",
    "pytest.ini",
    "release.sh",
    "release.ps1",
}

ALLOWED_ROOT_SUFFIXES = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".service",
    ".sql",
    ".sh",
    ".ps1",
    ".bat",
    ".example",
}


def _is_ignored(rel: Path) -> bool:
    parts = rel.parts
    return bool(parts) and parts[0] in IGNORED_ROOT_NAMES


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    bad: list[str] = []

    for rel in FORBIDDEN_DB:
        if _is_ignored(rel):
            continue
        p = root / rel
        if p.exists():
            bad.append(str(rel).replace("\\", "/"))

    for rel in FORBIDDEN_DIRS:
        if _is_ignored(rel):
            continue
        p = root / rel
        if p.exists():
            bad.append(str(rel).replace("\\", "/"))

    log_dir = root / FORBIDDEN_LOG_DIR
    if log_dir.exists():
        for pattern in FORBIDDEN_LOG_GLOBS:
            for p in log_dir.rglob(pattern):
                rel_path = p.relative_to(root)
                if p.is_file() and not _is_ignored(rel_path):
                    bad.append(str(rel_path).replace("\\", "/"))

    for p in root.iterdir():
        rel_path = p.relative_to(root)
        if _is_ignored(rel_path):
            continue
        if not p.is_file():
            continue
        if p.name.startswith(".") and p.name not in ALLOWED_ROOT_FILES:
            bad.append(str(rel_path).replace("\\", "/"))
            continue
        if p.name in ALLOWED_ROOT_FILES:
            continue
        if p.suffix in ALLOWED_ROOT_SUFFIXES:
            continue
        bad.append(str(rel_path).replace("\\", "/"))

    for glob_name in FORBIDDEN_DB_GLOBS:
        for p in root.rglob(glob_name):
            rel_path = p.relative_to(root)
            rel = str(rel_path).replace("\\", "/")
            if p.is_file() and not rel.startswith("dist/") and not _is_ignored(rel_path):
                bad.append(rel)

    for p in root.rglob("__pycache__"):
        rel_path = p.relative_to(root)
        if p.is_dir() and not _is_ignored(rel_path):
            bad.append(str(rel_path).replace("\\", "/"))

    for ext in ("*.pyc", "*.pyo"):
        for p in root.rglob(ext):
            rel_path = p.relative_to(root)
            if p.is_file() and not _is_ignored(rel_path):
                bad.append(str(rel_path).replace("\\", "/"))

    bad = [b for b in bad if not b.startswith("dist/")]

    if bad:
        print("❌ Release hygiene failed. Remove forbidden artifacts:")
        for b in sorted(set(bad))[:200]:
            print(f"  - {b}")
        if len(set(bad)) > 200:
            print(f"  ... and {len(set(bad)) - 200} more")
        return 2

    print("✅ Release hygiene OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
