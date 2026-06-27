from __future__ import annotations

from scripts import production_gate
from services import storage_legacy_audit


def test_storage_audit_skips_local_virtualenv_variants(tmp_path) -> None:
    venv_dir = tmp_path / ".venv-pr51" / "lib" / "python3.12" / "site-packages" / "vendor"
    venv_dir.mkdir(parents=True)
    (venv_dir / "leaked_sqlite.py").write_text("import sqlite3\nsqlite3.connect('vendor.db')\n", encoding="utf-8")

    project_file = tmp_path / "project_sqlite_probe.py"
    project_file.write_text("import sqlite3\nsqlite3.connect('project.db')\n", encoding="utf-8")

    findings = storage_legacy_audit._find_direct_sqlite_connects(tmp_path)

    assert [item.path for item in findings] == ["project_sqlite_probe.py"]


def test_production_gate_restore_target_reads_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("METRO_RESTORE_DRILL_DATABASE_URL", raising=False)
    monkeypatch.delenv("RESTORE_DATABASE_URL", raising=False)
    env_file = tmp_path / "metrotherapy.env"
    env_file.write_text("RESTORE_DATABASE_URL='postgresql://restore-user:secret@127.0.0.1:5432/metrotherapy_restore'\n", encoding="utf-8")

    gate_env = production_gate._merged_env(env_file)

    assert production_gate._restore_target_configured(gate_env)
    assert not production_gate._restore_target_configured({})
