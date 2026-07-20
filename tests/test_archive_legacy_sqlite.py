from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from scripts import archive_legacy_sqlite as archive_module
from scripts.archive_legacy_sqlite import _read_sqlite_metadata, archive_legacy_sqlite


def _make_sqlite(path: Path, value: str = "ok") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE demo(id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO demo(value) VALUES(?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _audit(monkeypatch: pytest.MonkeyPatch, db_path: Path, *, engine: str = "postgres") -> None:
    class _Audit:
        active_engine = engine
        database_url_configured = engine == "postgres"
        legacy_sqlite_path = str(db_path)

    import services.storage_legacy_audit as audit_module

    monkeypatch.setattr(audit_module, "storage_legacy_audit", lambda: _Audit())


def test_read_sqlite_metadata_accepts_valid_db(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _make_sqlite(db_path)

    ok, page_count, table_count, reason = _read_sqlite_metadata(db_path)

    assert ok is True
    assert page_count is not None and page_count > 0
    assert table_count == 1
    assert reason == ""


def test_read_sqlite_metadata_redacts_sqlite_error_text(tmp_path: Path) -> None:
    marker = "private-secret-marker"
    db_path = tmp_path / f"{marker}.db"
    db_path.write_bytes(b"not-a-database")

    ok, page_count, table_count, reason = _read_sqlite_metadata(db_path)

    assert ok is False
    assert page_count is None
    assert table_count is None
    assert reason == "sqlite_metadata_read_failed:SQLiteError"
    assert marker not in reason


def test_archive_legacy_sqlite_dry_run_does_not_move(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    _make_sqlite(db_path)
    _audit(monkeypatch, db_path)

    result = archive_legacy_sqlite(
        archive_dir=tmp_path / "archive",
        dry_run=True,
    )

    assert result.ok is True
    assert result.action == "archive"
    assert result.dry_run is True
    assert result.integrity_ok is True
    assert result.archive_verified is False
    assert result.source_removed is False
    assert db_path.exists()
    assert result.archive_path is not None
    assert not Path(result.archive_path).exists()


def test_archive_targets_are_unique_even_within_same_second(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    archive_dir = tmp_path / "archive"

    first = archive_module._archive_target(source, archive_dir)
    second = archive_module._archive_target(source, archive_dir)

    assert first != second
    assert first.parent == archive_dir
    assert second.parent == archive_dir


def test_archive_legacy_sqlite_apply_copies_verifies_then_removes_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    _make_sqlite(db_path, "source-data")
    _audit(monkeypatch, db_path)

    result = archive_legacy_sqlite(
        archive_dir=tmp_path / "archive",
        dry_run=False,
    )

    assert result.ok is True
    assert result.action == "archived"
    assert result.dry_run is False
    assert result.archive_verified is True
    assert result.source_removed is True
    assert db_path.exists() is False
    assert result.archive_path is not None
    archived = Path(result.archive_path)
    assert archived.exists()
    assert oct(os.stat(archived).st_mode & 0o777) == "0o600"
    ok, _pages, table_count, reason = _read_sqlite_metadata(archived)
    assert ok is True
    assert table_count == 1
    assert reason == ""
    assert list(archived.parent.glob(".*.partial")) == []


def test_existing_archive_collision_never_overwrites_and_keeps_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    archive_dir = tmp_path / "archive"
    fixed_target = archive_dir / "legacy.fixed.db"
    _make_sqlite(db_path, "source")
    _make_sqlite(fixed_target, "existing-archive")
    _audit(monkeypatch, db_path)
    monkeypatch.setattr(
        archive_module,
        "_archive_target",
        lambda _source, _archive_dir: fixed_target,
    )

    result = archive_legacy_sqlite(archive_dir=archive_dir, dry_run=False)

    assert result.ok is False
    assert result.action == "refuse"
    assert result.reason == "archive_target_collision"
    assert db_path.exists()
    with sqlite3.connect(str(fixed_target)) as conn:
        row = conn.execute("SELECT value FROM demo").fetchone()
    assert row == ("existing-archive",)
    assert list(archive_dir.glob(".*.partial")) == []


def test_source_change_after_copy_removes_candidate_and_keeps_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    archive_dir = tmp_path / "archive"
    _make_sqlite(db_path)
    _audit(monkeypatch, db_path)
    monkeypatch.setattr(archive_module, "_source_unchanged", lambda *_args: False)

    result = archive_legacy_sqlite(archive_dir=archive_dir, dry_run=False)

    assert result.ok is False
    assert result.reason == "legacy_sqlite_changed_during_archive"
    assert result.source_removed is False
    assert db_path.exists()
    assert list(archive_dir.glob("*.db")) == []
    assert list(archive_dir.glob(".*.partial")) == []


def test_copy_failure_removes_partial_and_preserves_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    archive_dir = tmp_path / "archive"
    _make_sqlite(db_path)
    _audit(monkeypatch, db_path)

    def fail_copy(_source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"partial")
        raise RuntimeError("archive_copy_failed")

    monkeypatch.setattr(archive_module, "_copy_database", fail_copy)

    result = archive_legacy_sqlite(archive_dir=archive_dir, dry_run=False)

    assert result.ok is False
    assert result.reason == "archive_copy_failed"
    assert db_path.exists()
    assert list(archive_dir.glob("*.db")) == []
    assert list(archive_dir.glob(".*.partial")) == []


def test_archive_legacy_sqlite_refuses_non_postgres(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    _make_sqlite(db_path)
    _audit(monkeypatch, db_path, engine="sqlite")

    result = archive_legacy_sqlite(
        archive_dir=tmp_path / "archive",
        dry_run=False,
    )

    assert result.ok is False
    assert result.action == "refuse"
    assert result.reason == "active_storage_is_not_confirmed_postgres"
    assert db_path.exists()


def test_cli_apply_requires_exact_legacy_inactive_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called = False

    def should_not_run(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("archive must not run without exact confirmation")

    monkeypatch.setattr(archive_module, "archive_legacy_sqlite", should_not_run)
    monkeypatch.setattr(
        sys,
        "argv",
        ["archive_legacy_sqlite.py", "--apply", "--json"],
    )

    assert archive_module.main() == 1
    payload = json.loads(capsys.readouterr().out)

    assert called is False
    assert payload["ok"] is False
    assert payload["dry_run"] is False
    assert payload["reason"] == "legacy_inactive_confirmation_invalid"


def test_cli_exact_confirmation_allows_apply_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_archive(*, archive_dir: Path | None, dry_run: bool):
        captured["archive_dir"] = archive_dir
        captured["dry_run"] = dry_run
        return archive_module.LegacySqliteArchivePlan(
            ok=True,
            action="archived",
            dry_run=False,
            source_path="legacy.db",
            archive_path="archive/legacy.db",
            active_engine="postgres",
            database_url_configured=True,
            integrity_ok=True,
            sqlite_page_count=1,
            sqlite_table_count=1,
            archive_verified=True,
            source_removed=True,
        )

    monkeypatch.setattr(archive_module, "archive_legacy_sqlite", fake_archive)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "archive_legacy_sqlite.py",
            "--apply",
            "--confirm-legacy-inactive",
            archive_module.ARCHIVE_CONFIRMATION,
            "--archive-dir",
            "/tmp/archive",
            "--json",
        ],
    )

    assert archive_module.main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert captured == {
        "archive_dir": Path("/tmp/archive"),
        "dry_run": False,
    }
    assert payload["archive_verified"] is True
    assert payload["source_removed"] is True
