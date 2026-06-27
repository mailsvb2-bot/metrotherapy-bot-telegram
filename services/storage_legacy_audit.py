from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.paths import DATABASE_URL, DB_PATH, ROOT
from services.db.runtime import CONFIG, redacted_db_target

# Direct sqlite3.connect is allowed only in explicit SQLite fallback/operator/test
# surfaces. Production business code must go through services.db.core.get_connection().
ALLOWED_DIRECT_SQLITE_CONNECT_PATHS = {
    "services/db/core.py",          # canonical DB adapter; Postgres branch wins in prod
    "services/db_writer.py",        # SQLite fallback writer; disabled in Postgres mode
    "scripts/archive_legacy_sqlite.py",  # operator-only archival/integrity tooling
    "scripts/backup_db.py",         # offline SQLite backup tooling
    "scripts/restore_db.py",        # offline SQLite restore tooling
    "scripts/restore_drill.py",     # offline SQLite restore drill
    "scripts/postgres_restore_drill.py",
    "scripts/stress_db.py",         # local SQLite stress tool
    "dashboard/sla_dashboard.py",   # standalone SQLite dashboard reader
    "check_db.py",                  # operator diagnostic
}

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "node_modules",
    "tests",
}
SKIP_DIR_PREFIXES = (".venv-", "venv-", "env-")


@dataclass(frozen=True)
class DirectSqliteConnect:
    path: str
    line: int
    expression: str


@dataclass(frozen=True)
class StorageLegacyAudit:
    active_engine: str
    db_target: str
    database_url_configured: bool
    legacy_sqlite_path: str | None
    legacy_sqlite_present: bool
    repo_local_sqlite_path: str
    repo_local_sqlite_present: bool
    direct_sqlite_connects: list[DirectSqliteConnect] = field(default_factory=list)
    disallowed_direct_sqlite_connects: list[DirectSqliteConnect] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.active_engine != "postgres":
            return "RED"
        if not self.database_url_configured:
            return "RED"
        if self.disallowed_direct_sqlite_connects:
            return "RED"
        if self.repo_local_sqlite_present:
            return "RED"
        if self.legacy_sqlite_present:
            return "YELLOW"
        return "GREEN"

    @property
    def marker(self) -> str:
        return {"GREEN": "✅", "YELLOW": "⚠️", "RED": "🛑"}.get(self.status, "⚠️")

    @property
    def active_storage_ok(self) -> bool:
        return self.active_engine == "postgres" and self.database_url_configured

    @property
    def hard_failures(self) -> list[str]:
        failures: list[str] = []
        if self.active_engine != "postgres":
            failures.append(f"active_engine_not_postgres:{self.active_engine}")
        if not self.database_url_configured:
            failures.append("database_url_missing")
        if self.repo_local_sqlite_present:
            failures.append(f"repo_local_sqlite_present:{self.repo_local_sqlite_path}")
        if self.disallowed_direct_sqlite_connects:
            failures.append("disallowed_direct_sqlite_connects")
        return failures

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.hard_failures,
            "status": self.status,
            "active_engine": self.active_engine,
            "db_target": self.db_target,
            "database_url_configured": self.database_url_configured,
            "legacy_sqlite_path": self.legacy_sqlite_path,
            "legacy_sqlite_present": self.legacy_sqlite_present,
            "repo_local_sqlite_path": self.repo_local_sqlite_path,
            "repo_local_sqlite_present": self.repo_local_sqlite_present,
            "direct_sqlite_connects": [item.__dict__ for item in self.direct_sqlite_connects],
            "disallowed_direct_sqlite_connects": [item.__dict__ for item in self.disallowed_direct_sqlite_connects],
            "hard_failures": self.hard_failures,
        }


def _is_sqlite_connect_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "connect":
        value = func.value
        return isinstance(value, ast.Name) and value.id == "sqlite3"
    return False


def _is_skipped_path(path: Path) -> bool:
    for part in path.parts:
        if part in SKIP_DIRS or part.startswith(SKIP_DIR_PREFIXES):
            return True
    return False


def _iter_python_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*.py"):
        if _is_skipped_path(path.relative_to(root)):
            continue
        result.append(path)
    return sorted(result)


def _source_segment(source: str, node: ast.AST) -> str:
    try:
        return ast.get_source_segment(source, node) or "sqlite3.connect(...)"
    except (IndexError, ValueError, TypeError):
        return "sqlite3.connect(...)"


def _find_direct_sqlite_connects(root: Path) -> list[DirectSqliteConnect]:
    findings: list[DirectSqliteConnect] = []
    for path in _iter_python_files(root):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if _is_sqlite_connect_call(node):
                findings.append(
                    DirectSqliteConnect(
                        path=rel,
                        line=int(getattr(node, "lineno", 0) or 0),
                        expression=_source_segment(source, node),
                    )
                )
    return findings


def storage_legacy_audit() -> StorageLegacyAudit:
    direct = _find_direct_sqlite_connects(ROOT)
    disallowed = [item for item in direct if item.path not in ALLOWED_DIRECT_SQLITE_CONNECT_PATHS]
    repo_local = ROOT / "data" / "data.db"
    legacy_path = Path(DB_PATH)
    return StorageLegacyAudit(
        active_engine=CONFIG.engine,
        db_target=redacted_db_target(),
        database_url_configured=bool((DATABASE_URL or "").strip()),
        legacy_sqlite_path=str(legacy_path) if CONFIG.uses_postgres else None,
        legacy_sqlite_present=bool(legacy_path.exists()) if CONFIG.uses_postgres else False,
        repo_local_sqlite_path=str(repo_local),
        repo_local_sqlite_present=repo_local.exists(),
        direct_sqlite_connects=direct,
        disallowed_direct_sqlite_connects=disallowed,
    )


def format_storage_legacy_audit_for_admin() -> str:
    audit = storage_legacy_audit()
    lines = [
        "🗄 Storage / legacy SQLite audit",
        "",
        f"Статус: {audit.marker} {audit.status}",
        f"Активный engine: {audit.active_engine}",
        f"DB target: {audit.db_target}",
        f"DATABASE_URL configured: {audit.database_url_configured}",
        f"Repo-local SQLite: {audit.repo_local_sqlite_path} present={audit.repo_local_sqlite_present}",
    ]
    if audit.legacy_sqlite_path:
        lines.append(f"Legacy SQLite artifact: {audit.legacy_sqlite_path} present={audit.legacy_sqlite_present}")
    lines.extend(
        [
            "",
            f"Direct sqlite3.connect points: {len(audit.direct_sqlite_connects)}",
            f"Disallowed direct sqlite3.connect points: {len(audit.disallowed_direct_sqlite_connects)}",
        ]
    )
    if audit.disallowed_direct_sqlite_connects:
        lines.append("")
        lines.append("Запрещённые прямые SQLite-точки:")
        for item in audit.disallowed_direct_sqlite_connects[:10]:
            lines.append(f"⚠️ {item.path}:{item.line} {item.expression[:120]}")
    if audit.status == "GREEN":
        lines.append("\nИтог: активная БД — Postgres, SQLite-двусмысленности не видно.")
    elif audit.status == "YELLOW":
        lines.append(
            "\nИтог: активная БД — Postgres, но legacy SQLite artifact ещё лежит на сервере. "
            "Удалять его можно только отдельным операторским шагом после backup/rollback решения."
        )
    else:
        lines.append("\nИтог: есть жёсткая storage-проблема. Релиз/cleanup нужно остановить до разбора.")
    return "\n".join(lines)


__all__ = [
    "ALLOWED_DIRECT_SQLITE_CONNECT_PATHS",
    "DirectSqliteConnect",
    "StorageLegacyAudit",
    "format_storage_legacy_audit_for_admin",
    "storage_legacy_audit",
]
