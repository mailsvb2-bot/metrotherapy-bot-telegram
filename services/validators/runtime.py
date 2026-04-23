from __future__ import annotations

import logging
import compileall
import os
import re
from pathlib import Path

IGNORED_SUBPATHS = {str(Path('services') / 'validators')}
from typing import Iterable

import sqlite3

from services.db import get_connection, DB_PATH
from core.paths import ROOT as PROJECT_ROOT

log = logging.getLogger(__name__)


from services.validators.base import ValidationError

def validate_background_tasks(strict: bool = False) -> None:
    """Detect forbidden `asyncio.create_task` usage outside the approved modules.

    This doesn't fail startup by default (strict=False) but prints a warning,
    so we don't accidentally add 'fire-and-forget' tasks that are hard to debug.
    """
    allow = {
        "core/task_manager.py",
        "scripts/validate_project.py",  # CLI-скрипт
        "runtime/health_server.py",
        "runtime/messenger_webhooks.py",
        "services/db_writer.py",
        "services/validator.py",
        "services/db/core.py",
        "services/scheduler.py",
        "services/db_writer.py",
        # This module contains the detection string itself.
        "services/validator.py",
    }

    base_dir = PROJECT_ROOT
    bad: list[str] = []
    for p in base_dir.rglob("*.py"):
        rel = str(p.relative_to(base_dir)).replace("\\", "/")
        if rel.startswith("services/validators/"):
            continue
        try:
            data = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if "asyncio.create_task" in data and rel not in allow:
            bad.append(rel)

    if bad:
        msg = f"Forbidden asyncio.create_task usage found in: {bad}. Use Scheduler jobs (v16.1)"
        if strict:
            raise ValidationError(msg)
        log.warning(msg)
def validate_single_scheduler(strict: bool = True) -> None:
    """Architectural guardrails (v16.4):

    - session_timers must never be imported from runtime code
    - scheduled_jobs must not be read/written outside schema migration / deprecated module
    - jobs pipeline must not rely on unix-int run_at/time.time/datetime.now
    """
    base_dir = PROJECT_ROOT

    # 1) Forbid importing services.session_timers anywhere except itself.
    bad_imports: list[str] = []
    import_re = re.compile(r"^\s*(from\s+services\.session_timers\s+import\b|import\s+services\.session_timers\b)")
    for p in base_dir.rglob("*.py"):
        rel = str(p.relative_to(base_dir)).replace("\\", "/")
        if rel in {"services/session_timers.py"}:
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for i, line in enumerate(txt.splitlines(), start=1):
            if import_re.search(line):
                bad_imports.append(f"{rel}:{i}")

    if bad_imports:
        msg = "Forbidden import of deprecated session_timers: " + ", ".join(bad_imports[:30])
        if strict:
            raise ValidationError(msg)
        log.warning(msg)

    # 2) Forbid READ/WRITE usage of scheduled_jobs table outside schema.py + deprecated module.
    allow = {"services/schema.py", "services/schema_tables.py", "services/migrations/scheduled_jobs_to_jobs_v1.py", "services/session_timers.py"}
    bad_scheduled: list[str] = []
    sql_re = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE)\b[^\n;]*\bscheduled_jobs\b", re.IGNORECASE)
    for p in base_dir.rglob("*.py"):
        rel = str(p.relative_to(base_dir)).replace("\\", "/")
        if rel in allow:
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if sql_re.search(txt):
            bad_scheduled.append(rel)

    if bad_scheduled:
        msg = f"Forbidden scheduled_jobs SQL usage found in: {sorted(set(bad_scheduled))}"
        if strict:
            raise ValidationError(msg)
        log.warning(msg)

    # 3) Forbid unix-int jobs timebase in jobs pipeline.
    jobs_pipeline = {"services/jobs.py", "core/engine.py"}
    unix_markers = ["time.time(", "datetime.now(", "run_at INTEGER", "run_at  INTEGER"]
    bad_unix: list[str] = []
    for p in base_dir.rglob("*.py"):
        rel = str(p.relative_to(base_dir)).replace("\\", "/")
        if rel not in jobs_pipeline:
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(m in txt for m in unix_markers):
            bad_unix.append(rel)

    if bad_unix:
        msg = f"Forbidden unix-time markers in jobs pipeline: {sorted(set(bad_unix))}"
        if strict:
            raise ValidationError(msg)
        log.warning(msg)
def validate_wide_except_policy(*, strict: bool = True) -> None:
    """Strict gate: forbid overly wide `except` blocks in business logic.

    We disallow:
    - bare `except:` (catches BaseException)
    - `except BaseException` / `except Exception`
    - very wide tuples like `except (OSError, RuntimeError, ...)`

    Allowed only in a small whitelist of last-resort boundary modules, and must be marked.
    """

    allow_files = {
        "main.py",
        "app.py",
        "core/engine.py",
        "core/middlewares.py",
        "services/scheduler.py",
        "core/task_manager.py",
        "core/ai/action_gateway.py",
        "core/ai/decision_core.py",
        "scripts/validate_project.py",  # CLI-скрипт
        "runtime/health_server.py",
        "runtime/messenger_webhooks.py",
        "services/db_writer.py",
        "services/validator.py",
        "services/db/core.py",
    }

    # Marker required even in allowed files, to keep occurrences intentional.
    marker = "validator: allow-wide-except"

    base_dir = PROJECT_ROOT
    bad: list[str] = []

    # Broad umbrella types that frequently hide bugs.
    forbidden_names = {"BaseException", "Exception"}

    bare_re = re.compile(r"^\s*except\s*:\s*(#.*)?$")
    single_re = re.compile(r"^\s*except\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")
    tuple_re = re.compile(r"^\s*except\s*\((?P<body>[^)]*)\)\s*:")

    for pth in base_dir.rglob("*.py"):
        rel = str(pth.relative_to(base_dir)).replace("\\", "/")
        if rel.startswith("services/validators/"):
            continue
        try:
            lines = pth.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        for i, line in enumerate(lines, start=1):
            # Bare except
            if bare_re.match(line):
                if rel not in allow_files:
                    bad.append(f"{rel}:{i} bare except not allowed")
                elif marker not in line:
                    bad.append(f"{rel}:{i} bare except missing marker")
                continue

            # Tuple except (potentially wide)
            tm = tuple_re.match(line)
            if tm:
                body = tm.group("body")
                # collect simple identifiers inside tuple
                names = [n.strip() for n in body.split(",") if n.strip()]
                # normalize names (strip module prefixes)
                simple = [n.split(".")[-1] for n in names]
                # Heuristic: tuple length >= 4 is considered wide.
                # Tuples with 2-3 narrow, domain-specific exceptions are common and acceptable.
                # Also consider wide if it includes both OSError and RuntimeError,
                # or contains forbidden umbrella types.
                is_wide = (
                    len(simple) >= 4
                    or ("OSError" in simple and "RuntimeError" in simple)
                    or any(n in forbidden_names for n in simple)
                )
                if is_wide:
                    if rel not in allow_files:
                        bad.append(f"{rel}:{i} wide tuple except not allowed: ({', '.join(simple)})")
                    elif marker not in line:
                        bad.append(f"{rel}:{i} wide tuple except missing marker")
                continue

            # Single-name except
            sm = single_re.match(line)
            if sm:
                name = sm.group("name")
                if name in forbidden_names:
                    if rel not in allow_files:
                        bad.append(f"{rel}:{i} except {name} not allowed")
                    elif marker not in line:
                        bad.append(f"{rel}:{i} except {name} missing marker")

    if bad:
        msg = "Wide-except policy failed: " + "; ".join(bad[:30])
        if strict:
            raise ValidationError(msg)
        log.warning(msg)
