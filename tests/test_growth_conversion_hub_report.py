from __future__ import annotations

import sqlite3
from pathlib import Path

from services import growth_conversion_hub


class _DbCtx:
    def __init__(self, path: Path):
        self.path = path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        assert self.conn is not None
        if exc_type is None:
            self.conn.commit()
        self.conn.close()
        return False


def test_conversion_hub_report_degrades_without_breaking_admin_callback(tmp_path, monkeypatch):
    path = tmp_path / "missing_conversion_schema.db"
    monkeypatch.setattr(growth_conversion_hub, "db", lambda: _DbCtx(path))

    text = growth_conversion_hub.build_conversion_hub_report("today")

    assert "Conversion Hub" in text
    assert "DEGRADED" in text
    assert "schema_not_migrated" in text
    assert "dispatch_allowed=False" in text
