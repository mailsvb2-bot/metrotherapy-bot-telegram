from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from services import admin_money_clients as money


class DbContext:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> Any:
        return self.conn

    def __exit__(self, *_args: Any) -> None:
        return None


class Cursor:
    def __init__(self, *, row: Any = None, rows: list[Any] | None = None) -> None:
        self._row = row
        self._rows = list(rows or [])

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class SequenceConn:
    def __init__(self, cursors: list[Cursor]) -> None:
        self.cursors = list(cursors)
        self.calls: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any = None) -> Cursor:
        self.calls.append((" ".join(query.split()), params))
        if not self.cursors:
            raise AssertionError(f"unexpected query: {query}")
        return self.cursors.pop(0)


def test_period_and_conversion_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    fixed = datetime(2026, 7, 20, 12, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(money, "_utc_now", lambda: fixed)
    assert money._period_start("today") == "2026-07-20T00:00:00+00:00"
    assert money._period_start("week") == "2026-07-13T12:30:00+00:00"
    assert money._period_start("month") == "2026-06-20T12:30:00+00:00"
    assert money._period_start("all") is None
    assert money._period_start("unknown") is None

    assert money._rowdict(None) is None
    assert money._rowdict({"x": 1}) == {"x": 1}
    assert money._rowdict([("x", 1)]) == {"x": 1}
    assert money._rowdict([("broken",)]) is None
    assert money._rows([{"a": 1}, None, [("b", 2)]]) == [{"a": 1}, {"b": 2}]

    assert money._amount_rub(12345, "RUB") == "123 RUB"
    assert money._amount_rub("bad", None) == "0 RUB"
    assert money._parse_dt("") is None
    assert money._parse_dt("bad") is None
    assert money._parse_dt("2026-07-20T10:00:00Z") == datetime(2026, 7, 20, 10, tzinfo=timezone.utc)
    assert money._parse_dt("2026-07-20T10:00:00") == datetime(2026, 7, 20, 10, tzinfo=timezone.utc)

    assert money._human_delta("bad", "bad") == "не посчитано"
    assert money._human_delta("2026-07-20T10:00:00Z", "2026-07-20T10:30:00Z") == "30 мин."
    assert money._human_delta("2026-07-20T10:00:00Z", "2026-07-20T12:15:00Z") == "2 ч. 15 мин."
    assert money._human_delta("2026-07-18T10:00:00Z", "2026-07-20T12:00:00Z") == "2 д. 2 ч."
    assert money._human_delta("2026-07-21T10:00:00Z", "2026-07-20T10:00:00Z") == "0 мин."


def test_safe_query_helpers() -> None:
    ok = SequenceConn([Cursor(row={"n": 4}), Cursor(rows=[{"x": 1}, [("y", 2)]])])
    assert money._safe_count(ok, "count", ()) == 4
    assert money._safe_rows(ok, "rows", ()) == [{"x": 1}, {"y": 2}]

    empty = SequenceConn([Cursor(row=None)])
    assert money._safe_count(empty, "count", ()) == 0

    bad_value = SequenceConn([Cursor(row={"n": "bad"})])
    assert money._safe_count(bad_value, "count", ()) == 0

    class FailingConn:
        def execute(self, _sql: str, _params: Any) -> Any:
            import sqlite3

            raise sqlite3.OperationalError("missing")

    assert money._safe_count(FailingConn(), "count", ()) == 0
    assert money._safe_rows(FailingConn(), "rows", ()) == []


def test_payment_where_and_period_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(money, "_period_start", lambda period: "START" if period != "all" else None)
    assert money._payment_where("week") == ("WHERE COALESCE(p.created_at, '') >= ?", ["START"])
    assert money._payment_where("all") == ("", [])

    conn = SequenceConn([
        Cursor(row={"n": 3, "amount": 12300}),
        Cursor(row={"n": 1}),
        Cursor(row={"n": 2}),
        Cursor(rows=[{"id": 9, "user_id": 7, "amount": 5000}]),
    ])
    monkeypatch.setattr(money, "db", lambda: DbContext(conn))
    summary = money.money_period_summary("nonsense", limit=5)
    assert summary == {
        "period": "today",
        "count": 3,
        "amount": 12300,
        "paid_users": 2,
        "problems": 1,
        "rows": [{"id": 9, "user_id": 7, "amount": 5000}],
    }
    assert conn.calls[0][1] == ("START",)
    assert conn.calls[3][1] == ("START", 5)

    conn = SequenceConn([
        Cursor(row={"n": 0, "amount": 0}),
        Cursor(row={"n": 0}),
        Cursor(row={"n": 0}),
        Cursor(rows=[]),
    ])
    monkeypatch.setattr(money, "db", lambda: DbContext(conn))
    summary = money.money_period_summary("all", limit=2)
    assert summary["period"] == "all"
    assert summary["rows"] == []
    assert conn.calls[0][1] == ()
    assert conn.calls[3][1] == (2,)


def test_event_meta_attribution_and_repeat_score() -> None:
    assert money._meta_dict({"x": 1}) == {"x": 1}
    assert money._meta_dict('{"utm_source":"ads"}') == {"utm_source": "ads"}
    assert money._meta_dict("[]") == {}
    assert money._meta_dict("bad") == {}
    assert money._meta_dict(None) == {}

    assert money._attribution_from_event(None) == {
        "source": "не подключено",
        "campaign": "не подключено",
        "creative": "не подключено",
        "ad_spend": "не подключено",
    }
    attribution = money._attribution_from_event({
        "meta": {"source": "telegram", "campaign": "summer", "utm_content": "banner", "cost": 100}
    })
    assert attribution == {
        "source": "telegram",
        "campaign": "summer",
        "creative": "banner",
        "ad_spend": "100",
    }

    low = money._repeat_purchase_score(sub={}, invited_count=0, gift_created=0, timeline_count=0)
    assert low["label"] == "низкая"
    medium = money._repeat_purchase_score(
        sub={"status": "active", "total_morning": 10, "used_morning": 1},
        invited_count=0,
        gift_created=0,
        timeline_count=0,
    )
    assert medium["label"] == "средняя"
    high = money._repeat_purchase_score(
        sub={"status": "active", "total_morning": 10, "used_morning": 8},
        invited_count=1,
        gift_created=0,
        timeline_count=3,
    )
    assert high["label"] == "высокая"
    assert "подарок или приглашение" in high["why"]


def test_find_first_start_event(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"name": "noise", "meta": "{}", "created_at": "1"},
        {"name": "custom", "meta": '{"utm_source":"ads"}', "created_at": "2"},
    ]
    monkeypatch.setattr(money, "_safe_rows", lambda *_args, **_kwargs: rows)
    event = money._find_first_start_event(object(), 7)
    assert event == {"name": "custom", "meta": {"utm_source": "ads"}, "created_at": "2"}

    monkeypatch.setattr(money, "_safe_rows", lambda *_args, **_kwargs: [{"name": "noise", "meta": "{}"}])
    assert money._find_first_start_event(object(), 7) == {"name": "noise", "meta": "{}"}
    monkeypatch.setattr(money, "_safe_rows", lambda *_args, **_kwargs: [])
    assert money._find_first_start_event(object(), 7) is None


def test_payment_client_card_not_found_and_full(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = SequenceConn([Cursor(row=None)])
    monkeypatch.setattr(money, "db", lambda: DbContext(conn))
    assert money.payment_client_card(9) == {"ok": False, "payment_id": 9, "reason": "payment_not_found"}

    payment = {
        "id": 9,
        "user_id": 7,
        "amount": 5000,
        "currency": "RUB",
        "payload": "sub:both",
        "created_at": "2026-07-20T12:00:00Z",
        "joined_at": "2026-07-20T10:00:00Z",
        "scope": "both",
        "plan_type": "both",
        "total_morning": 10,
        "total_evening": 10,
        "used_morning": 6,
        "used_evening": 5,
        "subscription_status": "active",
    }
    conn = SequenceConn([Cursor(row=payment)])
    monkeypatch.setattr(money, "db", lambda: DbContext(conn))
    monkeypatch.setattr(money, "_find_first_start_event", lambda _conn, _uid: {"meta": {"utm_source": "ads"}})
    counts = iter([2, 1, 4])
    monkeypatch.setattr(money, "_safe_count", lambda *_args, **_kwargs: next(counts))
    monkeypatch.setattr(money, "_safe_rows", lambda *_args, **_kwargs: [{"title": "Morning", "created_at": "now"}])
    monkeypatch.setattr(
        money,
        "user_card",
        lambda _uid: {
            "user": {"username": "person", "first_name": "Name"},
            "sub": {"scope": "legacy", "total_morning": 1},
            "invited_count": 1,
        },
    )
    card = money.payment_client_card(9)
    assert card["ok"] is True
    assert card["gift_created"] == 2
    assert card["gift_redeemed"] == 1
    assert card["timeline_count"] == 4
    assert card["last_audio"]["title"] == "Morning"
    assert card["repeat_purchase"]["label"] == "высокая"
    assert card["time_to_payment"] == "2 ч. 0 мин."
    assert card["user_card"]["sub"]["scope"] == "both"


def test_money_and_client_formatters() -> None:
    empty = money.format_money_period({
        "period": "today", "count": 0, "paid_users": 0, "amount": 0, "problems": 0, "rows": []
    })
    assert "Оплат за этот период пока нет" in empty

    text = money.format_money_period({
        "period": "week",
        "count": 1,
        "paid_users": 1,
        "amount": 5000,
        "problems": 1,
        "rows": [{
            "id": 9,
            "user_id": 7,
            "first_name": "Name",
            "username": "person",
            "amount": 5000,
            "currency": "RUB",
            "provider_status": "succeeded",
            "problem": "check",
        }],
    })
    assert "за 7 дней" in text
    assert "Name @person" in text
    assert "проверить: check" in text

    assert money.format_payment_client_card({"ok": False}) == "❌ Оплата не найдена."
    card_text = money.format_payment_client_card({
        "ok": True,
        "payment": {
            "id": 9,
            "user_id": 7,
            "amount": 5000,
            "currency": "RUB",
            "provider_status": "succeeded",
            "created_at": "now",
        },
        "user_card": {
            "user": {"first_name": "Name", "username": "person"},
            "sub": {"scope": "both", "used_morning": 2, "used_evening": 1, "total_morning": 5, "total_evening": 5},
            "invited_count": 3,
        },
        "attribution": {"source": "ads", "campaign": "summer", "creative": "c1", "ad_spend": "100"},
        "repeat_purchase": {"label": "высокая", "why": "active", "action": "renew"},
        "time_to_payment": "2 ч.",
        "timeline_count": 4,
        "last_audio": {"title": "Morning"},
        "gift_created": 2,
        "gift_redeemed": 1,
    })
    assert "Клиент: Name @person" in card_text
    assert "Прослушано/выдано по подписке: 3/10" in card_text
    assert "Вероятность: высокая" in card_text
