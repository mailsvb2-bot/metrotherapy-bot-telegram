from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from scripts.archive_legacy_sqlite import _read_sqlite_metadata, archive_legacy_sqlite


def _make_sqlite(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE demo(id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO demo(value) VALUES('ok')")
        conn.commit()
    finally:
        conn.close()


def test_read_sqlite_metadata_accepts_valid_db(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _make_sqlite(db_path)

    ok, page_count, table_count, reason = _read_sqlite_metadata(db_path)

    assert ok is True
    assert page_count is not None and page_count > 0
    assert table_count == 1
    assert reason == ""


def test_archive_legacy_sqlite_dry_run_does_not_move(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _make_sqlite(db_path)

    class _Audit:
        active_engine = "postgres"
        database_url_configured = True
        legacy_sqlite_path = str(db_path)

    import services.storage_legacy_audit as audit_module

    monkeypatch.setattr(audit_module, "storage_legacy_audit", lambda: _Audit())

    result = archive_legacy_sqlite(archive_dir=tmp_path / "archive", dry_run=True)

    assert result.ok is True
    assert result.action == "archive"
    assert result.dry_run is True
    assert result.integrity_ok is True
    assert db_path.exists()
    assert result.archive_path is not None
    assert not Path(result.archive_path).exists()


def test_archive_legacy_sqlite_apply_moves_file(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _make_sqlite(db_path)

    class _Audit:
        active_engine = "postgres"
        database_url_configured = True
        legacy_sqlite_path = str(db_path)

    import services.storage_legacy_audit as audit_module

    monkeypatch.setattr(audit_module, "storage_legacy_audit", lambda: _Audit())

    result = archive_legacy_sqlite(archive_dir=tmp_path / "archive", dry_run=False)

    assert result.ok is True
    assert result.action == "archived"
    assert result.dry_run is False
    assert db_path.exists() is False
    assert result.archive_path is not None
    assert Path(result.archive_path).exists()
    assert oct(os.stat(Path(result.archive_path)).st_mode & 0o777) == "0o600"


def test_archive_legacy_sqlite_refuses_non_postgres(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _make_sqlite(db_path)

    class _Audit:
        active_engine = "sqlite"
        database_url_configured = False
        legacy_sqlite_path = str(db_path)

    import services.storage_legacy_audit as audit_module

    monkeypatch.setattr(audit_module, "storage_legacy_audit", lambda: _Audit())

    result = archive_legacy_sqlite(archive_dir=tmp_path / "archive", dry_run=False)

    assert result.ok is False
    assert result.action == "refuse"
    assert result.reason == "active_storage_is_not_confirmed_postgres"
    assert db_path.exists()
