from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from services import growth_conversion_event_bridge as bridge
from services import growth_conversion_hub
from services.growth_conversion_event_bridge_core import map_event_to_conversion
from services.migrations.growth_conversion_bridge_state_v2 import apply as apply_bridge_state_v2
from services.migrations.growth_conversion_outbox_v1 import apply as apply_outbox_v1


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
        else:
            self.conn.rollback()
        self.conn.close()
        return False


def _fake_db(path: Path):
    return _DbCtx(path)


def _prepare(path: Path) -> None:
    with _fake_db(path) as conn:
        conn.execute(
            """
            CREATE TABLE events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event TEXT,
                ts TEXT,
                name TEXT,
                meta TEXT,
                created_at TEXT
            )
            """
        )
        apply_outbox_v1(conn)
        apply_bridge_state_v2(conn)


def _insert_event(conn, *, user_id: int, name: str = "", event: str = "", meta=None) -> int:
    cursor = conn.execute(
        "INSERT INTO events(user_id, name, event, meta, created_at) VALUES(?,?,?,?,?)",
        (int(user_id), name, event, json.dumps(meta or {}), "2026-07-10T10:00:00+00:00"),
    )
    return int(cursor.lastrowid)


def test_event_bridge_core_maps_only_canonical_downstream_events():
    mapped = map_event_to_conversion(
        {"id": 7, "user_id": 42, "name": "demo_ack", "meta": '{"kind":"work"}'},
        attribution={"source": "telegram_ads", "campaign": "may"},
    )

    assert mapped is not None
    assert mapped["conversion_type"] == "demo_ack"
    assert mapped["external_event_id"] == "events:7"
    assert mapped["attribution"]["source"] == "telegram_ads"
    assert mapped["payload"]["event_meta"]["kind"] == "work"
    assert map_event_to_conversion({"id": 8, "user_id": 42, "name": "demo_sent"}) is None


def test_bridge_uses_attribution_preceding_each_event_and_advances_cursor(tmp_path, monkeypatch):
    path = tmp_path / "event_bridge.db"
    _prepare(path)
    monkeypatch.setattr(bridge, "db", lambda: _fake_db(path))

    with _fake_db(path) as conn:
        _insert_event(
            conn,
            user_id=42,
            name="funnel_start_command",
            meta={"source": "telegram_ads", "campaign": "old", "creative": "reels1"},
        )
        demo_event_id = _insert_event(conn, user_id=42, name="demo_ack", meta={"kind": "work"})
        _insert_event(
            conn,
            user_id=42,
            name="funnel_start_command",
            meta={"source": "partner", "campaign": "new", "creative": "post1"},
        )
        tariff_event_id = _insert_event(conn, user_id=42, event="sub_menu_open", meta={"scope": "morning"})

    result = bridge.run_event_conversion_bridge_once(batch_size=100)

    assert result.processed == 2
    assert result.inserted == 2
    assert result.duplicates == 0
    assert result.last_event_id == tariff_event_id

    with _fake_db(path) as conn:
        rows = conn.execute(
            "SELECT external_event_id, conversion_type, attribution_json, dispatch_allowed "
            "FROM growth_conversion_outbox ORDER BY id"
        ).fetchall()
        state = conn.execute(
            "SELECT last_event_id, last_batch_size, last_inserted FROM growth_conversion_bridge_state"
        ).fetchone()

    assert rows[0]["external_event_id"] == f"events:{demo_event_id}"
    assert rows[0]["conversion_type"] == "demo_ack"
    assert json.loads(rows[0]["attribution_json"])["campaign"] == "old"
    assert rows[1]["external_event_id"] == f"events:{tariff_event_id}"
    assert rows[1]["conversion_type"] == "tariff_open"
    assert json.loads(rows[1]["attribution_json"])["campaign"] == "new"
    assert all(row["dispatch_allowed"] == 0 for row in rows)
    assert state["last_event_id"] == tariff_event_id
    assert state["last_batch_size"] == 2
    assert state["last_inserted"] == 2

    second = bridge.run_event_conversion_bridge_once(batch_size=100)
    assert second.processed == 0
    assert second.inserted == 0
    assert second.last_event_id == tariff_event_id


def test_bridge_batch_rolls_back_outbox_and_cursor_together(tmp_path, monkeypatch):
    path = tmp_path / "event_bridge_rollback.db"
    _prepare(path)
    monkeypatch.setattr(bridge, "db", lambda: _fake_db(path))

    with _fake_db(path) as conn:
        _insert_event(conn, user_id=7, name="funnel_start_command", meta={"source": "telegram_ads"})
        _insert_event(conn, user_id=7, name="demo_ack")
        _insert_event(conn, user_id=7, name="sub_menu_open")

    original = growth_conversion_hub.enqueue_conversion_dry_run_tx
    calls = 0

    def _fail_second(conn, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic_bridge_failure")
        return original(conn, **kwargs)

    monkeypatch.setattr(bridge, "enqueue_conversion_dry_run_tx", _fail_second)

    with pytest.raises(RuntimeError, match="synthetic_bridge_failure"):
        bridge.run_event_conversion_bridge_once(batch_size=100)

    with _fake_db(path) as conn:
        outbox_count = conn.execute("SELECT COUNT(*) AS n FROM growth_conversion_outbox").fetchone()["n"]
        state_count = conn.execute("SELECT COUNT(*) AS n FROM growth_conversion_bridge_state").fetchone()["n"]

    assert outbox_count == 0
    assert state_count == 0


def test_bridge_safe_degrades_when_schema_is_missing(tmp_path, monkeypatch):
    path = tmp_path / "missing_bridge_schema.db"
    monkeypatch.setattr(bridge, "db", lambda: _fake_db(path))

    result = bridge.run_event_conversion_bridge_safe()

    assert result.processed == 0
    assert "schema_not_migrated" in result.error
