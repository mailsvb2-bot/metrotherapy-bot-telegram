from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts import backup_db, restore_db



def _write_value(path: Path, value: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute('CREATE TABLE demo(value TEXT)')
        conn.execute('INSERT INTO demo(value) VALUES (?)', (value,))
        conn.commit()
    finally:
        conn.close()



def _read_value(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute('SELECT value FROM demo').fetchone()
        assert row is not None
        return str(row[0])
    finally:
        conn.close()



def test_backup_and_restore_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / 'data' / 'data.db'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _write_value(db_path, 'before')

    monkeypatch.setattr(backup_db, 'DB_PATH', db_path)
    monkeypatch.setattr(backup_db, 'ROOT', tmp_path)
    monkeypatch.setattr(restore_db, 'DB_PATH', db_path)
    monkeypatch.setattr(restore_db, 'ROOT', tmp_path)

    assert backup_db.main() == 0
    backup_file = restore_db._latest_backup()
    assert backup_file is not None and backup_file.exists()

    db_path.unlink()
    assert restore_db.main([]) == 0
    assert _read_value(db_path) == 'before'



def test_backup_prunes_old_files(tmp_path):
    backup_dir = tmp_path / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(5):
        (backup_dir / f'data_2026-01-01_00-00-0{idx}.db').write_text(str(idx), encoding='utf-8')

    removed = backup_db._prune_old_backups(backup_dir, keep=2)
    assert removed == 3
    names = sorted(p.name for p in backup_dir.glob('data_*.db'))
    assert names == [
        'data_2026-01-01_00-00-03.db',
        'data_2026-01-01_00-00-04.db',
    ]


def test_restore_verify_and_safety_copy(tmp_path, monkeypatch):
    db_path = tmp_path / 'data' / 'data.db'
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _write_value(db_path, 'current')

    backup_path = tmp_path / 'backups' / 'data_2026-01-01_00-00-00.db'
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    _write_value(backup_path, 'restored')

    monkeypatch.setattr(restore_db, 'DB_PATH', db_path)
    monkeypatch.setattr(restore_db, 'ROOT', tmp_path)

    assert restore_db.main(['--from-path', str(backup_path), '--verify']) == 0
    assert _read_value(db_path) == 'restored'
    safety = sorted((tmp_path / 'backups').glob('pre_restore_*.db'))
    assert safety, 'expected pre-restore safety backup to be created'
    assert _read_value(safety[-1]) == 'current'
