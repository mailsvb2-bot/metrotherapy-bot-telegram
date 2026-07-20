from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from services import audio_cache, audio_guard


@contextmanager
def connection_context(conn: sqlite3.Connection):
    yield conn


def test_audio_cache_round_trip_and_compatibility_wrappers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE audio_cache(path TEXT, kind TEXT, file_id TEXT, updated_at_utc TEXT, PRIMARY KEY(path, kind))"
    )

    assert audio_cache.get_file_id(conn, "a.ogg", "work") is None
    audio_cache.set_file_id(conn, "a.ogg", "work", "file-1")
    assert audio_cache.get_file_id(conn, "a.ogg", "work") == "file-1"

    audio_cache.save_cached_file_id(conn, "home:b.opus", "file-2")
    assert audio_cache.get_cached_file_id(conn, "home:b.opus") == "file-2"

    audio_cache.save_cached_file_id(conn, "plain.ogg", "file-3")
    assert audio_cache.get_cached_file_id(conn, "plain.ogg") == "file-3"

    audio_path = tmp_path / "nested" / "track.mp3"
    monkeypatch.setattr(audio_cache, "get_db", lambda: connection_context(conn))
    audio_cache.save_cached_file_id(audio_path, "demo", "file-4")
    assert audio_cache.get_cached_file_id(audio_path, "demo") == "file-4"


def test_audio_cache_missing_table_is_safe_and_other_errors_propagate() -> None:
    missing = sqlite3.connect(":memory:")
    assert audio_cache.get_file_id(missing, "x", "work") is None
    audio_cache.set_file_id(missing, "x", "work", "id")

    class BrokenConn:
        def execute(self, *_args: Any, **_kwargs: Any) -> Any:
            raise sqlite3.OperationalError("disk I/O error")

    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        audio_cache.get_file_id(BrokenConn(), "x", "work")
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        audio_cache.set_file_id(BrokenConn(), "x", "work", "id")

    assert audio_cache._table_missing(sqlite3.OperationalError("no such table: audio_cache"))
    assert audio_cache._table_missing(sqlite3.OperationalError("relation does not exist"))
    assert not audio_cache._table_missing(sqlite3.OperationalError("locked"))
    assert audio_cache._key_from_path_kind("/a/b/c.ogg", "WORK") == ("c.ogg", "WORK")


def test_audio_guard_scan_and_demo_selection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "z.ogg").write_bytes(b"z")
    (demo / "a.opus").write_bytes(b"a")
    (demo / "ignore.txt").write_text("x", encoding="utf-8")

    monkeypatch.setattr(audio_guard, "DEMO_DIR", demo)
    monkeypatch.setattr(audio_guard, "EXTS", {".ogg", ".opus"})
    assert [p.name for p in audio_guard._scan(demo)] == ["a.opus", "z.ogg"]
    assert audio_guard._scan(tmp_path / "missing") == []

    (demo / "work.ogg").write_bytes(b"work")
    assert audio_guard.pick_demo_file("work").name == "work.ogg"

    pair = tmp_path / "pair"
    pair.mkdir()
    (pair / "01.ogg").write_bytes(b"1")
    (pair / "02.opus").write_bytes(b"2")
    monkeypatch.setattr(audio_guard, "DEMO_DIR", pair)
    assert audio_guard.pick_demo_file("invalid").name == "01.ogg"
    assert audio_guard.pick_demo_file("home").name == "02.opus"

    (pair / "03.ogg").write_bytes(b"3")
    assert audio_guard.pick_demo_file("work") is None


def test_audio_guard_accepts_exotic_file_with_exact_stem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    demo = tmp_path / "exotic"
    demo.mkdir()
    exotic = demo / "home.custom"
    exotic.write_bytes(b"audio")
    monkeypatch.setattr(audio_guard, "DEMO_DIR", demo)
    monkeypatch.setattr(audio_guard, "EXTS", {".ogg", ".opus"})
    assert audio_guard.pick_demo_file("HOME") == exotic


def test_audio_guard_logs_demo_directory_scan_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class BrokenDemoDir:
        def __truediv__(self, name: str) -> Path:
            return tmp_path / "missing" / name

        def iterdir(self):
            raise OSError("broken demo directory")

        def exists(self) -> bool:
            return False

    monkeypatch.setattr(audio_guard, "DEMO_DIR", BrokenDemoDir())
    monkeypatch.setattr(audio_guard, "EXTS", {".ogg", ".opus"})
    assert audio_guard.pick_demo_file("work") is None
    assert "DEMO_DIR scan failed" in caplog.text


def test_audio_guard_full_catalog_access(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    full = tmp_path / "full"
    full.mkdir()
    monkeypatch.setattr(audio_guard, "FULL_DIR", full)
    monkeypatch.setattr(audio_guard, "EXTS", {".ogg", ".opus"})

    monkeypatch.setattr(audio_guard, "has_access", lambda *_args, **_kwargs: False)
    denied = audio_guard.get_full_files_guarded(7, required_scope="both")
    assert denied.ok is False
    assert "Подписка" in (denied.message or "")

    monkeypatch.setattr(audio_guard, "has_access", lambda *_args, **_kwargs: True)
    empty = audio_guard.get_full_files_guarded(7)
    assert empty.ok is False
    assert "не найдены" in (empty.message or "")

    (full / "b.opus").write_bytes(b"b")
    (full / "a.ogg").write_bytes(b"a")
    allowed = audio_guard.get_full_files_guarded(7)
    assert allowed.ok is True
    assert [p.name for p in allowed.paths or []] == ["a.ogg", "b.opus"]
