from __future__ import annotations

from services.migrations import messenger_media_assets_mtime_double_v7 as migration


class _Conn:
    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql: str, *_args, **_kwargs):
        self.sql.append(str(sql))
        return self


def test_postgres_media_asset_mtime_promoted_to_double_precision(monkeypatch):
    conn = _Conn()
    marked: list[str] = []

    monkeypatch.setattr(migration, "migration_applied", lambda _conn, _name: False)
    monkeypatch.setattr(migration, "_is_postgres", lambda: True)
    monkeypatch.setattr(migration, "mark_migration", lambda _conn, name: marked.append(name))

    migration.apply(conn)  # type: ignore[arg-type]

    joined = "\n".join(conn.sql)
    assert "ALTER TABLE messenger_media_assets" in joined
    assert "asset_mtime TYPE DOUBLE PRECISION" in joined
    assert marked == [migration.NAME]


def test_sqlite_media_asset_mtime_migration_is_noop_but_marked(monkeypatch):
    conn = _Conn()
    marked: list[str] = []

    monkeypatch.setattr(migration, "migration_applied", lambda _conn, _name: False)
    monkeypatch.setattr(migration, "_is_postgres", lambda: False)
    monkeypatch.setattr(migration, "mark_migration", lambda _conn, name: marked.append(name))

    migration.apply(conn)  # type: ignore[arg-type]

    assert conn.sql == []
    assert marked == [migration.NAME]
