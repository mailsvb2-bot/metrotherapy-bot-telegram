from __future__ import annotations

import sqlite3
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import pytest

from services import pricing_update


class Result:
    def __init__(self, row: Any = None, *, rowcount: int = 0) -> None:
        self.row = row
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self.row


class Conn:
    def __init__(
        self,
        *,
        old: Any = None,
        update_count: int = 1,
        select_error: BaseException | None = None,
        history_error: BaseException | None = None,
    ) -> None:
        self.old = old
        self.update_count = update_count
        self.select_error = select_error
        self.history_error = history_error
        self.calls: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any) -> Result:
        self.calls.append((query, params))
        if query.startswith("SELECT price"):
            if self.select_error:
                raise self.select_error
            return Result(self.old)
        if query.startswith("UPDATE plans"):
            return Result(rowcount=self.update_count)
        if query.startswith("INSERT INTO plan_price_history"):
            if self.history_error:
                raise self.history_error
            return Result(rowcount=1)
        return Result()


class DbContext:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> Any:
        return self.conn

    def __exit__(self, *_args: Any) -> None:
        return None


def plans() -> list[dict[str, Any]]:
    return [
        {"id": 1, "code": "basic", "title": "Базовый тариф", "price": 100},
        {"id": 2, "code": "premium", "title": "Премиум — Всё включено", "price": 200},
        {"id": 0, "code": "", "title": "Без кода", "price": 0},
        {"id": 3, "code": "empty-title", "title": "", "price": 300},
    ]


def install_plans(monkeypatch: pytest.MonkeyPatch, value: list[dict[str, Any]] | None = None) -> None:
    import services.plans

    monkeypatch.setattr(
        services.plans,
        "get_plans",
        lambda include_inactive=True: list(plans() if value is None else value),
    )


def test_normalization_and_table_missing() -> None:
    assert pricing_update._norm_title("  ПРЕМИУМ\u00a0— Ёж! ") == "премиум еж"
    assert pricing_update._norm_title("") == ""
    assert pricing_update._table_missing(sqlite3.OperationalError("no such table: x")) is True
    assert pricing_update._table_missing(RuntimeError("relation does not exist")) is True
    assert pricing_update._table_missing(RuntimeError("locked")) is False


def test_set_plan_price_validation_and_success_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    assert pricing_update.set_plan_price("x", "bad", conn=Conn()) is False
    assert pricing_update.set_plan_price("x", 0, conn=Conn()) is False
    assert pricing_update.set_plan_price("x", pricing_update.MAX_PRICE_RUB + 1, conn=Conn()) is False

    changed = Conn(old=(100,), update_count=1)
    assert pricing_update.set_plan_price("basic", 150, conn=changed, changed_by=7) is True
    history = [call for call in changed.calls if call[0].startswith("INSERT")]
    assert history and history[0][1][0:3] == ("basic", 100, 150)
    assert history[0][1][-1] == 7

    same = Conn(old={"price": 150}, update_count=0)
    assert pricing_update.set_plan_price("basic", 150, conn=same) is True
    assert not any(query.startswith("INSERT") for query, _ in same.calls)

    no_old = Conn(old=None, update_count=1)
    assert pricing_update.set_plan_price("new", 120, conn=no_old) is True

    missing_history = Conn(
        old=(100,), update_count=1,
        history_error=sqlite3.OperationalError("no such table: plan_price_history"),
    )
    assert pricing_update.set_plan_price("basic", 180, conn=missing_history) is True

    broken_history = Conn(
        old=(100,), update_count=1,
        history_error=sqlite3.OperationalError("locked"),
    )
    assert pricing_update.set_plan_price("basic", 180, conn=broken_history) is True

    select_sql_error = Conn(select_error=sqlite3.OperationalError("read"), update_count=0)
    assert pricing_update.set_plan_price("basic", 150, conn=select_sql_error) is False
    select_row_error = Conn(old={"wrong": 1}, update_count=0)
    assert pricing_update.set_plan_price("basic", 150, conn=select_row_error) is False

    via_db = Conn(old=(10,), update_count=1)
    monkeypatch.setattr(pricing_update, "db", lambda: DbContext(via_db))
    assert pricing_update.set_plan_price_by_code("basic", 20) is True


def test_set_price_by_title_exact_partial_fuzzy_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_plans(monkeypatch)
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        pricing_update,
        "set_plan_price",
        lambda *, code, price, conn=None, changed_by=None: calls.append((code, price)) or True,
    )
    assert pricing_update.set_plan_price_by_title("БАЗОВЫЙ тариф", 111) is True
    assert calls[-1] == ("basic", 111)
    assert pricing_update.set_plan_price_by_title("премиум", 222) is True
    assert calls[-1] == ("premium", 222)
    assert pricing_update.set_plan_price_by_title("Базовыи тариф", 333) is True
    assert calls[-1] == ("basic", 333)

    install_plans(monkeypatch, [])
    with pytest.raises(ValueError, match="Тарифов нет"):
        pricing_update.set_plan_price_by_title("x", 1)

    install_plans(monkeypatch, [{"code": "abc", "title": "Совсем другое"}])
    with pytest.raises(ValueError, match="не найден"):
        pricing_update.set_plan_price_by_title("xyz", 1)

    install_plans(monkeypatch, [{"code": "", "title": "Точный"}])
    with pytest.raises(ValueError):
        pricing_update.set_plan_price_by_title("Точный", 1)


def test_batch_price_updates_matching_and_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    install_plans(monkeypatch)
    conn = Conn()
    monkeypatch.setattr(pricing_update, "db", lambda: DbContext(conn))
    applied_codes: list[str] = []

    def apply(*, code: str, price: int, conn: Any, changed_by: int | None = None) -> bool:
        applied_codes.append(code)
        return code != "premium" or price != 999

    monkeypatch.setattr(pricing_update, "set_plan_price", apply)
    applied, missing = pricing_update.set_plan_prices_by_titles(
        [
            ("Базовый тариф", 110),
            ("премиум", 220),
            ("Базовыи тариф", 330),
            ("не существует", 440),
            ("Премиум Всё включено", 999),
        ],
        changed_by=5,
    )
    assert applied == 3
    assert "не существует" in missing
    assert "Премиум Всё включено" in missing
    assert applied_codes[:3] == ["basic", "premium", "basic"]

    install_plans(monkeypatch, [{"code": "", "title": ""}])
    applied, missing = pricing_update.set_plan_prices_by_titles([("x", 1)])
    assert applied == 0 and missing == ["x"]


def test_verbose_updates_cover_code_id_title_fuzzy_and_read_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_plans(monkeypatch)

    class VerboseConn:
        def __init__(self) -> None:
            self.reads = 0

        def execute(self, query: str, params: Any) -> Result:
            if query.startswith("SELECT"):
                self.reads += 1
                if self.reads == 5:
                    raise sqlite3.OperationalError("read")
                if self.reads == 6:
                    return Result({"bad": 1})
                return Result({"price": 100 + self.reads})
            return Result(rowcount=1)

    conn = VerboseConn()
    monkeypatch.setattr(pricing_update, "db", lambda: DbContext(conn))
    applied_codes: list[str] = []
    monkeypatch.setattr(
        pricing_update,
        "set_plan_price",
        lambda *, code, price, conn, changed_by=None: applied_codes.append(code) or code != "premium-fail",
    )

    applied, missing, details = pricing_update.set_plan_prices_by_titles_verbose(
        [
            ("basic", 150),
            ("2", 250),
            ("Базовый тариф", 350),
            ("премиум", 450),
            ("Базовыи тариф", 550),
            ("не существует", 650),
            ("premium", 750),
        ],
        changed_by=9,
    )
    assert applied == 6
    assert missing == ["не существует"]
    assert len(details) == 7
    assert details[0]["code"] == "basic"
    assert details[1]["code"] == "premium"
    assert details[5] == {
        "input": "не существует", "title": "", "code": "",
        "old": None, "new": 650, "changed": False,
    }
    assert any(detail["old"] is None for detail in details)
    assert set(applied_codes) == {"basic", "premium"}
