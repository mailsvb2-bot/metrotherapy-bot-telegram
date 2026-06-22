from __future__ import annotations

"""Archive the legacy SQLite artifact after Postgres migration.

The script is intentionally conservative:
- loads the production env file before importing project storage settings;
- refuses to run unless active storage is Postgres with DATABASE_URL configured;
- verifies the SQLite artifact can be opened read-only and passes integrity_check;
- defaults to dry-run and only moves the file with --apply;
- moves, never deletes, so rollback evidence remains available.
"""

import argparse
import json
import os
import shlex
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = Path("/etc/metrotherapy/metrotherapy.env")
DEFAULT_ARCHIVE_DIRNAME = "legacy_sqlite_archive"


@dataclass(frozen=True)
class LegacySqliteArchivePlan:
    ok: bool
    action: str
    dry_run: bool
    source_path: str | None
    archive_path: str | None
    active_engine: str
    database_url_configured: bool
    integrity_ok: bool | None
    sqlite_page_count: int | None
    sqlite_table_count: int | None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "action": self.action,
            "dry_run": self.dry_run,
            "source_path": self.source_path,
            "archive_path": self.archive_path,
            "active_engine": self.active_engine,
            "database_url_configured": self.database_url_configured,
            "integrity_ok": self.integrity_ok,
            "sqlite_page_count": self.sqlite_page_count,
            "sqlite_table_count": self.sqlite_table_count,
            "reason": self.reason,
        }


def _load_env_file(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        try:
            parts = shlex.split(value, posix=True)
            loaded[key] = parts[0] if len(parts) == 1 else value
        except ValueError:
            loaded[key] = value.strip('"').strip("'")
    return loaded


def _apply_env(loaded: dict[str, str]) -> None:
    for key, value in loaded.items():
        os.environ.setdefault(str(key), str(value))


def _read_sqlite_metadata(path: Path) -> tuple[bool, int | None, int | None, str]:
    uri = "file:" + str(path) + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        return False, None, None, f"sqlite_open_failed:{type(exc).__name__}:{exc}"
    try:
        integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
        integrity_ok = bool(integrity_row and str(integrity_row[0]).lower() == "ok")
        page_row = conn.execute("PRAGMA page_count").fetchone()
        table_row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()
        page_count = int(page_row[0]) if page_row and page_row[0] is not None else None
        table_count = int(table_row[0]) if table_row and table_row[0] is not None else None
        if not integrity_ok:
            return False, page_count, table_count, "sqlite_integrity_check_failed"
        return True, page_count, table_count, ""
    except sqlite3.Error as exc:
        return False, None, None, f"sqlite_integrity_query_failed:{type(exc).__name__}:{exc}"
    finally:
        conn.close()


def _archive_target(source: Path, archive_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return archive_dir / f"{source.stem}.{stamp}{source.suffix or '.db'}"


def build_archive_plan(*, archive_dir: Path | None, dry_run: bool) -> LegacySqliteArchivePlan:
    from services.storage_legacy_audit import storage_legacy_audit

    audit = storage_legacy_audit()
    if audit.active_engine != "postgres" or not audit.database_url_configured:
        return LegacySqliteArchivePlan(
            ok=False,
            action="refuse",
            dry_run=dry_run,
            source_path=audit.legacy_sqlite_path,
            archive_path=None,
            active_engine=audit.active_engine,
            database_url_configured=audit.database_url_configured,
            integrity_ok=None,
            sqlite_page_count=None,
            sqlite_table_count=None,
            reason="active_storage_is_not_confirmed_postgres",
        )
    if not audit.legacy_sqlite_path:
        return LegacySqliteArchivePlan(
            ok=True,
            action="noop",
            dry_run=dry_run,
            source_path=None,
            archive_path=None,
            active_engine=audit.active_engine,
            database_url_configured=audit.database_url_configured,
            integrity_ok=None,
            sqlite_page_count=None,
            sqlite_table_count=None,
            reason="no_legacy_path_in_postgres_mode",
        )
    source = Path(audit.legacy_sqlite_path)
    if not source.exists():
        return LegacySqliteArchivePlan(
            ok=True,
            action="noop",
            dry_run=dry_run,
            source_path=str(source),
            archive_path=None,
            active_engine=audit.active_engine,
            database_url_configured=audit.database_url_configured,
            integrity_ok=None,
            sqlite_page_count=None,
            sqlite_table_count=None,
            reason="legacy_sqlite_not_present",
        )

    integrity_ok, page_count, table_count, reason = _read_sqlite_metadata(source)
    if not integrity_ok:
        return LegacySqliteArchivePlan(
            ok=False,
            action="refuse",
            dry_run=dry_run,
            source_path=str(source),
            archive_path=None,
            active_engine=audit.active_engine,
            database_url_configured=audit.database_url_configured,
            integrity_ok=False,
            sqlite_page_count=page_count,
            sqlite_table_count=table_count,
            reason=reason,
        )

    target_dir = archive_dir or source.parent / DEFAULT_ARCHIVE_DIRNAME
    target = _archive_target(source, target_dir)
    return LegacySqliteArchivePlan(
        ok=True,
        action="archive",
        dry_run=dry_run,
        source_path=str(source),
        archive_path=str(target),
        active_engine=audit.active_engine,
        database_url_configured=audit.database_url_configured,
        integrity_ok=True,
        sqlite_page_count=page_count,
        sqlite_table_count=table_count,
    )


def archive_legacy_sqlite(*, archive_dir: Path | None = None, dry_run: bool = True) -> LegacySqliteArchivePlan:
    plan = build_archive_plan(archive_dir=archive_dir, dry_run=dry_run)
    if not plan.ok or plan.action != "archive" or dry_run:
        return plan

    source = Path(str(plan.source_path))
    target = Path(str(plan.archive_path))
    target.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target.parent, 0o700)
    shutil.move(str(source), str(target))
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass

    return LegacySqliteArchivePlan(
        ok=True,
        action="archived",
        dry_run=False,
        source_path=str(source),
        archive_path=str(target),
        active_engine=plan.active_engine,
        database_url_configured=plan.database_url_configured,
        integrity_ok=plan.integrity_ok,
        sqlite_page_count=plan.sqlite_page_count,
        sqlite_table_count=plan.sqlite_table_count,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive legacy SQLite artifact after confirmed Postgres migration")
    parser.add_argument("--apply", action="store_true", help="Move the legacy SQLite file into the archive directory")
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--env-file", default=os.getenv("METROTHERAPY_ENV_FILE", str(DEFAULT_ENV_FILE)))
    parser.add_argument("--archive-dir", default=os.getenv("METRO_LEGACY_SQLITE_ARCHIVE_DIR", ""))
    args = parser.parse_args()

    env_file = Path(args.env_file) if args.env_file else None
    loaded = _load_env_file(env_file)
    _apply_env(loaded)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    archive_dir = Path(args.archive_dir) if str(args.archive_dir or "").strip() else None
    result = archive_legacy_sqlite(archive_dir=archive_dir, dry_run=not bool(args.apply))
    payload = result.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"LEGACY_SQLITE_ARCHIVE_{mode} action={result.action} ok={result.ok}")
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    if not result.ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
