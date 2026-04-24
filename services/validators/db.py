from __future__ import annotations

EXCLUDED_SCAN_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    "site-packages",
    "dist-packages",
    "node_modules",
    "build",
    "dist",
    "logs",
}


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

def validate_no_real_db(*, strict: bool = True) -> None:
    """Critical hygiene rule: never ship a stateful DB file.

    We forbid shipping a real SQLite file in the repo/release. The application must
    create the DB from schema/migrations at runtime.

    Enforced when strict=True (default in prod/CI) and also during local runs,
    because a bundled DB silently breaks reproducibility.
    """
    # Historically the project used both layouts:
    # - ./data.db
    # - ./data/data.db
    # Any shipped SQLite state breaks reproducibility and is a data leak risk.
    candidates = [
        PROJECT_ROOT / "data.db",
        PROJECT_ROOT / "data" / "data.db",
    ]

    found = [p for p in candidates if p.exists()]
    if not found:
        return

    # Allow templates only (empty placeholder files).
    found = [p for p in found if not p.name.endswith(".template")]

    details = []
    for p in found:
        # skip validator self-scan
        if "services/validators/" in str(p).replace("\\","/"):
            continue
        if "services/validators" in p.as_posix():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            size = -1
        details.append(f"{p} (size={size})")

    msg_strict = "Release hygiene failed: stateful DB file(s) must not be shipped: " + ", ".join(details)
    if strict:
        raise ValidationError(msg_strict)
    # В dev-режиме мы не должны пугать словом "failed" — это всего лишь предупреждение.
    msg = "Release hygiene warning: stateful DB file(s) present (remove before release): " + ", ".join(details)
    log.warning(msg)
def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}  # name is at index 1
def validate_db_schema(strict: bool = True) -> None:
    # DB is created by init_db(), so here we only verify contracts.
    required_tables = {
        "users",
        "plans",
        "plan_price_history",
        "payments",
        "selected_plan",
        "mood_sessions",
    }

    with get_connection() as conn:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = sorted(required_tables - existing)
        if missing:
            msg = f"DB schema missing required tables: {missing}. DB: {DB_PATH}"
            if strict:
                raise ValidationError(msg)
            log.warning(msg)

        # Payments must have these
        if "payments" in existing:
            cols = _table_columns(conn, "payments")
            required_cols = {"id", "user_id", "payload", "amount", "currency", "created_at"}
            miss_cols = sorted(required_cols - cols)
            if miss_cols:
                msg = f"DB schema table payments missing columns: {miss_cols}. Existing: {sorted(cols)}"
                if strict:
                    raise ValidationError(msg)
                log.warning(msg)
def validate_schema_decomposition(strict: bool = True) -> None:
    """Guardrails for schema decomposition (v16.8).

    Rules:
    - services/schema_tables.py must not be imported from runtime code.
      Only services/schema_core.py is allowed to import it.
    - Runtime modules must not perform schema migration SQL (DDL / schema_migrations writes).
      Allowed only in services/schema_core.py and services/migrations/*.
    """

    base_dir = PROJECT_ROOT

    # 1) schema_tables import only from schema_core
    allow_importers = {"services/schema_core.py", "services/schema_tables.py"}
    import_re = re.compile(r"^\s*(from\s+services\.schema_tables\s+import\b|import\s+services\.schema_tables\b)")
    bad: list[str] = []
    for p in base_dir.rglob("*.py"):
        rel = str(p.relative_to(base_dir)).replace("\\", "/")
        if rel.startswith("services/validators/") or rel.startswith("tests/"):
            continue
        if rel in allow_importers:
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except OSError:
            continue
        # Best-effort line number
        for i, line in enumerate(txt.splitlines(), start=1):
            if import_re.search(line):
                bad.append(f"{rel}:{i}")
                break

    if bad:
        msg = "Forbidden import of schema_tables (only schema_core may import it): " + ", ".join(bad[:30])
        if strict:
            raise ValidationError(msg)
        log.warning(msg)

    # 2) forbid schema-migration SQL from runtime modules
    # Allowed: schema_core + migrations/*
    allow_sql = {"services/schema_core.py", "services/schema_tables.py", "services/validator.py", "services/db/core.py"}
    # Also allow decomposed schema modules
    # (DDL lives here by design; runtime modules must not import them directly).
    allow_sql_prefixes = ("services/db/schema/",)

    # All files under services/migrations are allowed.
    ddl_re = re.compile(r"\b(CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE)\b", re.IGNORECASE)
    mig_re = re.compile(r"\b(schema_migrations)\b", re.IGNORECASE)
    bad_sql: list[str] = []
    for p in base_dir.rglob("*.py"):
        rel = str(p.relative_to(base_dir)).replace("\\", "/")
        if rel.startswith("services/validators/") or rel.startswith("tests/"):
            continue
        if rel in allow_sql or rel.startswith("services/migrations/") or rel.startswith(allow_sql_prefixes):
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if ddl_re.search(txt) or mig_re.search(txt):
            bad_sql.append(rel)

    if bad_sql:
        msg = f"Forbidden migration/DDL SQL in runtime modules: {sorted(set(bad_sql))}"
        if strict:
            raise ValidationError(msg)
        log.warning(msg)
