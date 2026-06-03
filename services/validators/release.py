from __future__ import annotations

import logging
import os
from pathlib import Path

from core.paths import ROOT as PROJECT_ROOT
from services.validators.base import ValidationError

log = logging.getLogger(__name__)

_SKIP_HYGIENE_PARTS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    "build",
    "dist",
    ".tox",
}


def _is_under_skipped_dir(path: Path) -> bool:
    try:
        parts = set(path.relative_to(PROJECT_ROOT).parts)
    except ValueError:
        return True
    return bool(parts & _SKIP_HYGIENE_PARTS)


def _project_files(pattern: str) -> list[Path]:
    return [p for p in PROJECT_ROOT.rglob(pattern) if not _is_under_skipped_dir(p)]


def validate_release_hygiene(*, strict: bool = True) -> None:
    """Release gate to keep the application tree clean.

    The gate intentionally ignores virtualenv/build/cache directories. A deployed
    server may have `.venv` inside the project path, and Python/package tooling can
    legitimately keep pyc files there. What must stay clean is the application
    source/runtime tree that is shipped and executed.
    """
    release_mode = os.getenv("VALIDATOR_RELEASE_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
    if not strict and not release_mode:
        return

    pycache_dirs = [p for p in _project_files("__pycache__") if p.is_dir()]
    pyc_files = [p for p in _project_files("*.pyc") if p.is_file()]
    pyo_files = [p for p in _project_files("*.pyo") if p.is_file()]
    env_files = [p for p in _project_files(".env*") if p.is_file() and p.name != ".env.example"]
    runtime_db = [
        p for p in _project_files("*")
        if p.is_file() and (p.suffix in {".db", ".sqlite"} or p.name.endswith((".db-wal", ".db-shm", ".db-journal")))
    ]
    logs = [p for p in _project_files("*.log") if p.is_file()]

    bad = pycache_dirs + pyc_files + pyo_files + env_files + runtime_db + logs
    if not bad:
        return

    sample = ", ".join(str(p.relative_to(PROJECT_ROOT)).replace("\\", "/") for p in bad[:40])
    msg = (
        "Release hygiene failed: "
        f"__pycache__ dirs={len(pycache_dirs)}, .pyc files={len(pyc_files)}, .pyo files={len(pyo_files)}, "
        f"env files={len(env_files)}, runtime db files={len(runtime_db)}, log files={len(logs)}. "
        f"Sample: {sample}"
    )
    if strict:
        raise ValidationError(msg)
    log.warning(msg)


def validate_compileall(*, strict: bool = True) -> None:
    """Validate Python source syntax without writing __pycache__/pyc files."""
    release_mode = os.getenv("VALIDATOR_RELEASE_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
    force = os.getenv("VALIDATOR_COMPILEALL", "").strip().lower() in {"1", "true", "yes", "on"}
    if not (strict or release_mode or force):
        return
    if os.getenv("VALIDATOR_SKIP_COMPILEALL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return

    errors: list[str] = []
    for path in PROJECT_ROOT.rglob("*.py"):
        if _is_under_skipped_dir(path):
            continue
        rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, rel, "exec")
        except (OSError, SyntaxError) as exc:
            errors.append(f"{rel}: {exc}")
    if errors:
        msg = "Source compile failed: " + "; ".join(errors[:20])
        if strict:
            raise ValidationError(msg)
        log.warning(msg)
