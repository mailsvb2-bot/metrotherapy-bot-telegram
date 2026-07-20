from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import backup_db


def _write_database(path: Path, value: str = "value") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE sample(value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample(value) VALUES(?)", (value,))
        conn.commit()


def _payload(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    return json.loads(capsys.readouterr().out)


def _configure(monkeypatch: pytest.MonkeyPatch, *, root: Path, source: Path) -> None:
    monkeypatch.setattr(backup_db, "ROOT", root)
    monkeypatch.setattr(backup_db, "DB_PATH", source)
    monkeypatch.setattr(backup_db, "is_postgres_enabled", lambda: False)


def test_missing_source_returns_nonzero_without_creating_backup_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "data" / "missing.db"
    _configure(monkeypatch, root=tmp_path, source=source)

    assert backup_db.main() == 2
    payload = _payload(capsys)

    assert payload["ok"] is False
    assert payload["published"] is False
    assert payload["error_code"] == "source_not_found"
    assert not (tmp_path / "backups").exists()


def test_two_fast_backups_are_unique_verified_and_leave_no_partials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "data" / "data.db"
    _write_database(source, "source")
    _configure(monkeypatch, root=tmp_path, source=source)

    paths: list[Path] = []
    for _ in range(2):
        assert backup_db.main() == 0
        payload = _payload(capsys)
        path = Path(str(payload["backup_path"]))
        paths.append(path)
        assert payload["ok"] is True
        assert payload["source_quick_check_ok"] is True
        assert payload["backup_integrity_ok"] is True
        assert int(payload["size_bytes"]) > 0
        assert path.is_file()
        backup_db._check_database(path, quick=False)

    assert paths[0] != paths[1]
    assert list((tmp_path / "backups").glob(".*.partial")) == []


def test_corrupt_source_is_rejected_before_backup_directory_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "data" / "data.db"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"not-a-sqlite-database")
    _configure(monkeypatch, root=tmp_path, source=source)

    assert backup_db.main() == 2
    payload = _payload(capsys)

    assert payload["published"] is False
    assert payload["error_code"] == "source_quick_check_unavailable"
    assert not (tmp_path / "backups").exists()


def test_copy_failure_cleans_partial_and_does_not_publish_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "data" / "data.db"
    _write_database(source)
    _configure(monkeypatch, root=tmp_path, source=source)

    def fail_copy(_source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"partial")
        raise backup_db.BackupDbError("forced_copy_failure")

    monkeypatch.setattr(backup_db, "_copy_database", fail_copy)

    assert backup_db.main() == 2
    payload = _payload(capsys)

    assert payload["error_code"] == "forced_copy_failure"
    backup_dir = tmp_path / "backups"
    assert list(backup_dir.glob("data_*.db")) == []
    assert list(backup_dir.glob(".*.partial")) == []


def test_final_verification_failure_removes_published_candidate_and_skips_prune(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "data" / "data.db"
    _write_database(source)
    _configure(monkeypatch, root=tmp_path, source=source)
    original_check = backup_db._check_database
    prune_called = False

    def fail_final_target(path: Path, *, quick: bool) -> None:
        if path.parent == tmp_path / "backups" and path.name.startswith("data_"):
            raise backup_db.BackupDbError("forced_final_verification_failure")
        original_check(path, quick=quick)

    def record_prune(_backup_dir: Path, _keep: int) -> int:
        nonlocal prune_called
        prune_called = True
        return 0

    monkeypatch.setattr(backup_db, "_check_database", fail_final_target)
    monkeypatch.setattr(backup_db, "_prune_old_backups", record_prune)

    assert backup_db.main() == 2
    payload = _payload(capsys)

    assert payload["error_code"] == "forced_final_verification_failure"
    assert prune_called is False
    backup_dir = tmp_path / "backups"
    assert list(backup_dir.glob("data_*.db")) == []
    assert list(backup_dir.glob(".*.partial")) == []


def test_backup_keep_is_bounded_and_invalid_value_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKUP_KEEP", "broken")
    assert backup_db._backup_keep() == 14

    monkeypatch.setenv("BACKUP_KEEP", "-5")
    assert backup_db._backup_keep() == 0

    monkeypatch.setenv("BACKUP_KEEP", "999999")
    assert backup_db._backup_keep() == 10_000
