from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from services import admin_tariffs


class DbContext:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> Any:
        return self.conn

    def __exit__(self, *_args: Any) -> None:
        return None


class Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def fetchall(self) -> list[Any]:
        return self.rows


def test_payload_day_and_mpl_helpers(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    assert admin_tariffs._parse_plan_code_from_payload(None) is None
    assert admin_tariffs._parse_plan_code_from_payload("other") is None
    assert admin_tariffs._parse_plan_code_from_payload("sub: basic :30") == "basic"
    assert admin_tariffs._parse_plan_code_from_payload("sub::30") is None
    assert admin_tariffs._to_day(None) is None
    assert admin_tariffs._to_day("short") is None
    assert admin_tariffs._to_day("2026-07-20T12:00:00") == "2026-07-20"
    assert admin_tariffs._day_to_dt("2026-07-20").day == 20

    admin_tariffs._MPL_READY = True
    admin_tariffs._ensure_mpl()
    admin_tariffs._MPL_READY = False
    import matplotlib

    used: list[str] = []
    monkeypatch.setattr(matplotlib, "use", lambda backend: used.append(backend))
    monkeypatch.delenv("MPLCONFIGDIR", raising=False)
    admin_tariffs._ensure_mpl()
    assert used == ["Agg"]
    assert admin_tariffs._MPL_READY is True
    assert os.environ.get("MPLCONFIGDIR")


class Axis:
    def __init__(self) -> None:
        self.xaxis = SimpleNamespace(
            get_major_locator=lambda: "locator",
            set_major_locator=lambda value: None,
            set_major_formatter=lambda value: None,
        )

    def set_title(self, value: str) -> None:
        return None

    def set_xlabel(self, value: str) -> None:
        return None

    def set_ylabel(self, value: str) -> None:
        return None

    def plot(self, *args: Any, **kwargs: Any) -> None:
        return None

    def bar(self, *args: Any, **kwargs: Any) -> None:
        return None

    def twinx(self) -> "Axis":
        return Axis()


class Figure:
    def autofmt_xdate(self, **kwargs: Any) -> None:
        return None

    def tight_layout(self) -> None:
        return None

    def savefig(self, buf: Any, **kwargs: Any) -> None:
        buf.write(b"PNG")


class Plot:
    def __init__(self) -> None:
        self.closed: list[Any] = []

    def subplots(self, **kwargs: Any) -> tuple[Figure, Axis]:
        return Figure(), Axis()

    def close(self, fig: Any) -> None:
        self.closed.append(fig)


class PaymentsConn:
    def __init__(self, *, paid_at: bool = True, fail_cols: bool = False) -> None:
        self.paid_at = paid_at
        self.fail_cols = fail_cols

    def execute(self, query: str) -> Result:
        if query.startswith("PRAGMA"):
            if self.fail_cols:
                raise OSError("columns")
            return Result([(0, "id"), (1, "paid_at") if self.paid_at else (1, "created_at")])
        if "paid_at FROM" in query:
            return Result([
                ("sub:basic:30", "2026-07-18T10:00:00", "2026-07-20T10:00:00"),
                {"payload": "sub:premium:30", "created_at": "2026-07-19T10:00:00", "paid_at": "2026-07-20T11:00:00"},
                ("other", "2026-07-20T10:00:00", None),
                ("sub:basic", "short", None),
            ])
        return Result([("sub:basic:30", "2026-07-20T10:00:00")])


class HistoryConn:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def execute(self, query: str) -> Result:
        if self.fail:
            raise OSError("history")
        return Result([
            ("basic", 100, 120, "2026-07-19T10:00:00"),
            {"plan_code": "basic", "old_price": 120, "new_price": 150, "changed_at_utc": "2026-07-20T10:00:00"},
            ("premium", None, 250, "2026-07-20T12:00:00"),
            ("bad", 1, 2, "short"),
        ])


def test_build_tariff_dynamics_with_and_without_paid_at(monkeypatch: pytest.MonkeyPatch) -> None:
    plot = Plot()
    monkeypatch.setattr(admin_tariffs, "_plt", lambda: plot)
    connections = iter([PaymentsConn(paid_at=True), HistoryConn()])
    monkeypatch.setattr(admin_tariffs, "db", lambda: DbContext(next(connections)))
    images = admin_tariffs.build_tariff_dynamics_images([
        {"code": "basic", "title": "Базовый", "price": 180},
        {"code": "premium", "title": "Премиум", "price": "bad"},
        {"code": "empty", "title": "Нет данных", "price": 1},
        {"code": "", "title": "ignored", "price": 2},
    ])
    assert {caption for caption, _ in images} == {
        "Базовый — цена и оплаты", "Премиум — цена и оплаты"
    }
    assert all(payload == b"PNG" for _, payload in images)
    assert len(plot.closed) == 2

    plot2 = Plot()
    monkeypatch.setattr(admin_tariffs, "_plt", lambda: plot2)
    connections = iter([PaymentsConn(paid_at=False), HistoryConn(fail=True)])
    monkeypatch.setattr(admin_tariffs, "db", lambda: DbContext(next(connections)))
    images = admin_tariffs.build_tariff_dynamics_images([
        {"code": "basic", "title": "Базовый", "price": None},
    ])
    assert images == [("Базовый — цена и оплаты", b"PNG")]

    plot3 = Plot()
    monkeypatch.setattr(admin_tariffs, "_plt", lambda: plot3)
    connections = iter([PaymentsConn(fail_cols=True), HistoryConn(fail=True)])
    monkeypatch.setattr(admin_tariffs, "db", lambda: DbContext(next(connections)))
    assert admin_tariffs.build_tariff_dynamics_images([]) == []
