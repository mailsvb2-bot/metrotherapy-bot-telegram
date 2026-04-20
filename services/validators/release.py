from __future__ import annotations

import logging
import compileall
import os
import re
from pathlib import Path
from typing import Iterable

import sqlite3

from services.db import get_connection, DB_PATH
from core.paths import ROOT as PROJECT_ROOT

log = logging.getLogger(__name__)


from services.validators.base import ValidationError

def validate_release_hygiene(*, strict: bool = True) -> None:
    """Release gate to keep distribution artifacts clean.

    If strict=True (default for prod/CI), it always runs.
    If strict=False, it runs only when VALIDATOR_RELEASE_MODE=1.
    """
    release_mode = os.getenv("VALIDATOR_RELEASE_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}
    if not strict and not release_mode:
        return

    pycache_dirs = [p for p in PROJECT_ROOT.rglob("__pycache__") if p.is_dir()]
    pyc_files = [p for p in PROJECT_ROOT.rglob("*.pyc") if p.is_file()]
    pyo_files = [p for p in PROJECT_ROOT.rglob("*.pyo") if p.is_file()]

    if not pycache_dirs and not pyc_files and not pyo_files:
        return

    msg = (
        "Release hygiene failed: "
        f"__pycache__ dirs={len(pycache_dirs)}, .pyc files={len(pyc_files)}, .pyo files={len(pyo_files)}"
    )
    if strict:
        raise ValidationError(msg)
    log.warning(msg)
def validate_compileall(*, strict: bool = True) -> None:
    """Compile all project .py files to catch SyntaxError/import-time issues early.

    Runs only in release/strict mode unless VALIDATOR_COMPILEALL=1 is set.
    """
    release_mode = os.getenv("VALIDATOR_RELEASE_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
    force = os.getenv("VALIDATOR_COMPILEALL", "").strip().lower() in {"1", "true", "yes", "on"}
    if not (strict or release_mode or force):
        return
    if os.getenv("VALIDATOR_SKIP_COMPILEALL", "").strip().lower() in {"1", "true", "yes", "on"}:
        return

    # Exclude common non-source folders to keep it fast.
    rx = re.compile(r"(\\\\|/)(\.git|\.venv|venv|__pycache__|\.mypy_cache|\.pytest_cache|build|dist|\.tox)(\\\\|/)")
    ok = compileall.compile_dir(
        str(PROJECT_ROOT),
        quiet=1,
        rx=rx,
        force=False,
    )
    if not ok:
        msg = "Compileall failed: some .py files did not compile (see log above)"
        if strict:
            raise ValidationError(msg)
        log.warning(msg)
