from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from services import plan_store, plans


class DbContext:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> Any:
        return self.conn

    def __exit__(self, *_args: Any) -> None:
        return None


class Cursor:
    def __init__(self, *, row: Any = None, rows: list[Any] | None = None) -> None:
        self.row = row
        self.rows = list(rows or [])

    def fetchone(self) -> Any:
        return self.row

    def fetchall(self) -> list[Any]:
        return list(self.rows)


class Conn:
    def __init__(self, cursors: list[Cursor] | None = None) -> None:
        self.cursors = list(cursors or [])
        self.calls: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any = None) -> Cursor:
        self.calls.append((" ".join(query.split()), params))
        return self.cursors.pop(0) if self.cursors else Cursor()


class MappingRow:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def keys(self):
        return self.data.keys()

    def __getitem__(self, key: Any) -> Any:
        return self.data[key]

    def __iter__(self):
        return iter(self.data.items())


def test_plan_row_helpers() -> None:
    assert plans._row_get(None, "x", 0, "default") == "default"
    assert plans._row_get({"x": 1}, "x", 0) == 1
    assert plans._row_get((2, 3), "x", 1) == 3
    assert plans._row_get({}, "x", 9, "fallback") == "fallback"

    assert plans._parse_int(None, field="x", default=7) == 7
    assert plans._parse_int(True, field="x") == 1
    assert plans._parse_int(5, field="x") == 5
    assert plans._parse_int(" 6 ", field="x") == 6
    assert plans._parse_int("bad", field="x", plan_id=1, default=9) == 9


def test_row_to_plan_tuple_mapping_and_bad_values() -> None:
    plan = plans._row_to_plan((1, "code", "Title", "both", "20", "4900", 1))
    assert plan == {
        "id": 1,
        "code": "code",
        "plan_code": "code",
        "title": "Title",
        "scope": "both",
        "days": 20,
        "price": 4900,
        "is_active": True,
    }

    bad = plans._row_to_plan(("bad", None, None, None, "oops", "price", 0))
    assert bad["id"] == 0
    assert bad["code"] == "None"
    assert bad["title"] == "None"
    assert bad["scope"] == "None"
    assert bad["days"] == 0
    assert bad["price"] == 0
    assert bad["is_active"] is False

    mapped = plans._row_to_plan(MappingRow({
        "id": 2,
        "code": "m",
        "title": "Mapped",
        "plan_type": "morning",
        "touches": 5,
        "price": 1000,
        "is_active": 1,
    }))
    assert mapped["id"] == 2
    assert mapped["scope"] == "morning"


def test_plan_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [(1, "a", "A", "both", 5, 1000, 1), (2, "b", "B", "home", 20, 2000, 0)]
    conn = Conn([Cursor(rows=rows), Cursor(rows=rows), Cursor(rows=[rows[0]]), Cursor(row=rows[0]), Cursor(row=None), Cursor(row=rows[0]), Cursor(row=None)])
    monkeypatch.setattr(plans, "db", lambda: DbContext(conn))

    active = plans.get_active_plans()
    assert [item["id"] for item in active] == [1, 2]
    assert "WHERE is_active=1" in conn.calls[0][0]

    all_plans = plans.get_plans(include_inactive=True)
    assert len(all_plans) == 2
    assert "WHERE is_active=1" not in conn.calls[1][0]

    only_active = plans.get_plans(include_inactive=False)
    assert len(only_active) == 1
    assert "WHERE is_active=1" in conn.calls[2][0]

    assert plans.get_plan_by_id(1)["code"] == "a"
    assert conn.calls[3][1] == (1,)
    assert plans.get_plan_by_id(99) is None

    assert plans.get_plan_by_scope_days("both", 5)["id"] == 1
    assert conn.calls[5][1] == ("both", 5)
    assert plans.get_plan_by_scope_days("none", 0) is None


def test_coerce_plan_id(monkeypatch: pytest.MonkeyPatch) -> None:
    assert plan_store._coerce_plan_id("both", 5, 7) == 7
    assert plan_store._coerce_plan_id("both", 5, "8") == 8
    assert plan_store._coerce_plan_id("both", 5, "bad") is None

    monkeypatch.setattr(plans, "get_plan_by_scope_days", lambda _scope, _days: {"id": 9})
    assert plan_store._coerce_plan_id("both", 5, None) == 9
    monkeypatch.setattr(plans, "get_plan_by_scope_days", lambda _scope, _days: {"plan_id": 10})
    assert plan_store._coerce_plan_id("both", 5, None) == 10
    monkeypatch.setattr(plans, "get_plan_by_scope_days", lambda _scope, _days: None)
    assert plan_store._coerce_plan_id("both", 5, None) is None
    monkeypatch.setattr(plans, "get_plan_by_scope_days", lambda _scope, _days: {"id": "bad"})
    assert plan_store._coerce_plan_id("both", 5, None) is None


def test_set_plan_canonical_pointer(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = Conn()
    monkeypatch.setattr(plan_store, "db", lambda: DbContext(conn))
    monkeypatch.setattr(plan_store, "utc_now", lambda: SimpleNamespace(replace=lambda **_kwargs: SimpleNamespace(isoformat=lambda: "NOW")))

    plan_store.set_plan(7, "both", 20, "Old", 4900, "legacy", plan_id=12)
    params = conn.calls[0][1]
    assert params == (7, 12, "both", 20, "", None, "", "NOW")

    monkeypatch.setattr(plan_store, "_coerce_plan_id", lambda *_args: None)
    plan_store.set_plan(8, "home", 5, "Legacy", 990, "home_5")
    params = conn.calls[1][1]
    assert params == (8, None, "home", 5, "Legacy", 990, "home_5", "NOW")


def test_get_plan_missing_legacy_and_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = Conn([Cursor(row=None)])
    monkeypatch.setattr(plan_store, "db", lambda: DbContext(conn))
    assert plan_store.get_plan(7) is None

    legacy = MappingRow({
        "user_id": 7,
        "plan_id": None,
        "scope": "both",
        "days": 5,
        "title": "Legacy",
        "price": 990,
        "plan_code": "legacy",
    })
    conn = Conn([Cursor(row=legacy)])
    monkeypatch.setattr(plan_store, "db", lambda: DbContext(conn))
    assert plan_store.get_plan(7)["title"] == "Legacy"

    bad_pointer = MappingRow({"user_id": 7, "plan_id": "bad", "scope": "both", "days": 5})
    conn = Conn([Cursor(row=bad_pointer)])
    monkeypatch.setattr(plan_store, "db", lambda: DbContext(conn))
    assert plan_store.get_plan(7)["plan_id"] == "bad"

    canonical = MappingRow({
        "user_id": 7,
        "plan_id": 12,
        "scope": "legacy",
        "days": 1,
        "title": "stale",
        "price": 1,
        "plan_code": "stale",
    })
    conn = Conn([Cursor(row=canonical)])
    monkeypatch.setattr(plan_store, "db", lambda: DbContext(conn))
    monkeypatch.setattr(
        plans,
        "get_plan_by_id",
        lambda pid: {
            "id": pid,
            "title": "Canonical",
            "price": 4900,
            "code": "both_20",
            "scope": "both",
            "days": 20,
        },
    )
    result = plan_store.get_plan(7)
    assert result["title"] == "Canonical"
    assert result["price"] == 4900
    assert result["plan_code"] == "both_20"
    assert result["scope"] == "both"
    assert result["days"] == 20

    conn = Conn([Cursor(row=canonical)])
    monkeypatch.setattr(plan_store, "db", lambda: DbContext(conn))
    monkeypatch.setattr(plans, "get_plan_by_id", lambda _pid: None)
    result = plan_store.get_plan(7)
    assert result["title"] == "stale"


def test_clear_and_get_plan_id(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = Conn()
    monkeypatch.setattr(plan_store, "db", lambda: DbContext(conn))
    plan_store.clear_plan(7)
    assert conn.calls[0][1] == (7,)

    monkeypatch.setattr(plan_store, "get_plan", lambda _uid: None)
    assert plan_store.get_plan_id(7) is None
    monkeypatch.setattr(plan_store, "get_plan", lambda _uid: {"plan_id": 12})
    assert plan_store.get_plan_id(7) == 12
    monkeypatch.setattr(plan_store, "get_plan", lambda _uid: {"plan_id": "bad"})
    assert plan_store.get_plan_id(7) is None
