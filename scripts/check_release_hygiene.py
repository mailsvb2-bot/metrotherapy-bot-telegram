"""Fast guardrail for git/pre-commit/CI.

Fails if the working tree contains artifacts that must never be shipped:
- __pycache__ directories
- *.pyc/*.pyo files
- runtime SQLite DB files (data.db, data/data.db)
- test/lint caches or runtime logs
- suspicious temporary root-level packaging fragments
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
    Path(".ruff_cache"),
    Path(".mypy_cache"),
    Path(".idea"),
    Path(".vscode"),
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
    "requirements.txt",
    "requirements-dev.txt",
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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    bad: list[str] = []

    for rel in FORBIDDEN_DB:
        p = root / rel
        if p.exists():
            bad.append(str(rel).replace("\\", "/"))

    for rel in FORBIDDEN_DIRS:
        p = root / rel
        if p.exists():
            bad.append(str(rel).replace("\\", "/"))

    log_dir = root / FORBIDDEN_LOG_DIR
    if log_dir.exists():
        for pattern in FORBIDDEN_LOG_GLOBS:
            for p in log_dir.rglob(pattern):
                if p.is_file():
                    bad.append(str(p.relative_to(root)).replace("\\", "/"))

    for p in root.iterdir():
        if not p.is_file():
            continue
        if p.name.startswith(".") and p.name not in ALLOWED_ROOT_FILES:
            bad.append(str(p.relative_to(root)).replace("\\", "/"))
            continue
        if p.name in ALLOWED_ROOT_FILES:
            continue
        if p.suffix in ALLOWED_ROOT_SUFFIXES:
            continue
        bad.append(str(p.relative_to(root)).replace("\\", "/"))

    for glob_name in FORBIDDEN_DB_GLOBS:
        for p in root.rglob(glob_name):
            rel = str(p.relative_to(root)).replace("\\", "/")
            if p.is_file() and not rel.startswith("dist/"):
                bad.append(rel)

    for p in root.rglob("__pycache__"):
        if p.is_dir():
            bad.append(str(p.relative_to(root)).replace("\\", "/"))

    for ext in ("*.pyc", "*.pyo"):
        for p in root.rglob(ext):
            if p.is_file():
                bad.append(str(p.relative_to(root)).replace("\\", "/"))

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
