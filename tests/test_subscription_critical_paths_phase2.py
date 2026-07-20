from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from services import practice_tokens, subscription


class DbContext:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> Any:
        return self.conn

    def __exit__(self, *_args: Any) -> None:
        return None


class Cursor:
    def __init__(self, *, row: Any = None, rowcount: int = 0) -> None:
        self._row = row
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self._row


class ScriptedConn:
    def __init__(self, responses: list[Cursor]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any = None) -> Cursor:
        self.calls.append((" ".join(query.split()), params))
        if not self.responses:
            raise AssertionError(f"unexpected query: {query}")
        return self.responses.pop(0)


def make_subscription(**overrides: Any) -> subscription.Subscription:
    base = subscription.Subscription(
        user_id=7,
        plan_type="both",
        total_morning=3,
        total_evening=4,
        used_morning=1,
        used_evening=2,
        status="active",
        started_at="2026-07-20T00:00:00+00:00",
        scope="both",
    )
    return replace(base, **overrides)


def test_subscription_properties_and_row_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    active = make_subscription()
    assert active.remaining_morning == 2
    assert active.remaining_evening == 2
    assert active.is_finished is False

    finished = replace(active, used_morning=99, used_evening=99)
    assert finished.remaining_morning == 0
    assert finished.remaining_evening == 0
    assert finished.is_finished is True

    conn = ScriptedConn([
        Cursor(row=(7, "both", 3, 4, 1, 2, "active", "started", "both")),
    ])
    monkeypatch.setattr(subscription, "db", lambda: DbContext(conn))
    loaded = subscription.get_subscription(7)
    assert loaded == subscription.Subscription(7, "both", 3, 4, 1, 2, "active", "started", "both")

    conn = ScriptedConn([Cursor(row=None)])
    monkeypatch.setattr(subscription, "db", lambda: DbContext(conn))
    assert subscription.get_subscription(8) is None


def test_token_authoritative_access_and_wallet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(practice_tokens, "token_access_authoritative", lambda: True)
    monkeypatch.setattr(practice_tokens, "has_paid_practice_access", lambda user_id: user_id == 7)
    monkeypatch.setattr(practice_tokens, "get_wallet", lambda user_id: SimpleNamespace(available_tokens=5))

    assert subscription.is_active(7) is True
    assert subscription.is_active(8) is False
    assert subscription.get_scope(7) == "both"
    assert subscription.get_scope(8) is None
    assert subscription.has_access(7, "morning") is True
    assert subscription.has_access(7, "evening") is True
    assert subscription.has_access(7, "both") is True
    assert subscription.has_access(7, "other") is False
    assert subscription.remaining(7) == (5, 5)
    assert subscription.register_touch(7, "morning") is False


def test_legacy_access_scope_remaining_and_slots(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subscription, "_token_product_authoritative", lambda: False)

    monkeypatch.setattr(subscription, "get_subscription", lambda _uid: None)
    assert subscription.is_active(1) is False
    assert subscription.get_scope(1) is None
    assert subscription.has_access(1, "morning") is False
    assert subscription.remaining(1) == (0, 0)

    inactive = make_subscription(status="finished")
    monkeypatch.setattr(subscription, "get_subscription", lambda _uid: inactive)
    assert subscription.is_active(1) is False
    assert subscription.get_scope(1) is None
    assert subscription.has_access(1, "morning") is False

    active = make_subscription()
    monkeypatch.setattr(subscription, "get_subscription", lambda _uid: active)
    assert subscription.is_active(1) is True
    assert subscription.get_scope(1) == "both"
    assert subscription.has_access(1, "morning") is True
    assert subscription.has_access(1, "evening") is True
    assert subscription.has_access(1, "both") is True
    assert subscription.has_access(1, "invalid") is False
    assert subscription.remaining(1) == (2, 2)

    morning = make_subscription(scope=None, total_evening=0, used_evening=0)
    monkeypatch.setattr(subscription, "get_subscription", lambda _uid: morning)
    assert subscription.get_scope(1) == "morning"

    evening = make_subscription(scope="legacy", total_morning=0, used_morning=0)
    monkeypatch.setattr(subscription, "get_subscription", lambda _uid: evening)
    assert subscription.get_scope(1) == "evening"

    empty = make_subscription(scope="legacy", total_morning=0, total_evening=0, used_morning=0, used_evening=0)
    monkeypatch.setattr(subscription, "get_subscription", lambda _uid: empty)
    assert subscription.get_scope(1) is None


def test_grant_wrapper_uses_one_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = object()
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(subscription, "db", lambda: DbContext(conn))
    monkeypatch.setattr(
        subscription,
        "grant_tx",
        lambda *args, **kwargs: calls.append((*args, kwargs)),
    )

    subscription.grant(7, "both", 30, price=990, source="gift", gift_id="g-1")
    assert calls == [(conn, 7, "both", 30, {"price": 990, "source": "gift", "gift_id": "g-1"})]


def test_grant_tx_inserts_new_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subscription, "_now_utc", lambda: SimpleNamespace(isoformat=lambda: "NOW"))
    conn = ScriptedConn([Cursor(row=None), Cursor(rowcount=1)])

    subscription.grant_tx(conn, 7, "morning", 10)

    assert len(conn.calls) == 2
    insert_sql, params = conn.calls[1]
    assert "INSERT INTO subscriptions" in insert_sql
    assert params == (7, "morning", 10, 0, 0, 0, "active", "NOW", "morning", "NOW", "NOW")


def test_grant_tx_reactivates_finished_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subscription, "_now_utc", lambda: SimpleNamespace(isoformat=lambda: "NOW"))
    conn = ScriptedConn([
        Cursor(row=(2, 3, 2, 3, "finished")),
        Cursor(rowcount=1),
    ])

    subscription.grant_tx(conn, 7, "both", 5)

    update_sql, params = conn.calls[1]
    assert "status='active'" in update_sql
    assert params == ("both", 7, 8, 0, 0, "NOW", "both", "NOW", 7)


def test_grant_tx_extends_active_subscription(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subscription, "_now_utc", lambda: SimpleNamespace(isoformat=lambda: "NOW"))
    conn = ScriptedConn([
        Cursor(row=(2, 3, 1, 1, "active")),
        Cursor(rowcount=1),
    ])

    subscription.grant_tx(conn, 7, "evening", 4)

    update_sql, params = conn.calls[1]
    assert "SET plan_type=?" in update_sql
    assert params == ("evening", 2, 7, "evening", "NOW", 7)


@pytest.mark.parametrize(
    ("slot", "usage_column"),
    [("morning", "used_morning"), ("evening", "used_evening")],
)
def test_register_touch_updates_slot_and_finishes_when_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    slot: str,
    usage_column: str,
) -> None:
    monkeypatch.setattr(subscription, "_token_product_authoritative", lambda: False)
    conn = ScriptedConn([
        Cursor(rowcount=1),
        Cursor(row=(1, 1, 1, 1)),
        Cursor(rowcount=1),
    ])
    monkeypatch.setattr(subscription, "db", lambda: DbContext(conn))

    assert subscription.register_touch(7, slot) is True
    assert usage_column in conn.calls[0][0]
    assert "status='finished'" in conn.calls[2][0]


def test_register_touch_rejection_and_missing_followup_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subscription, "_token_product_authoritative", lambda: False)
    assert subscription.register_touch(7, "invalid") is False

    conn = ScriptedConn([Cursor(rowcount=0)])
    monkeypatch.setattr(subscription, "db", lambda: DbContext(conn))
    assert subscription.register_touch(7, "morning") is False

    conn = ScriptedConn([Cursor(rowcount=1), Cursor(row=None)])
    monkeypatch.setattr(subscription, "db", lambda: DbContext(conn))
    assert subscription.register_touch(7, "evening") is True
    assert len(conn.calls) == 2


def test_register_touch_does_not_finish_with_remaining_capacity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subscription, "_token_product_authoritative", lambda: False)
    conn = ScriptedConn([Cursor(rowcount=1), Cursor(row=(3, 3, 1, 3))])
    monkeypatch.setattr(subscription, "db", lambda: DbContext(conn))

    assert subscription.register_touch(7, "morning") is True
    assert len(conn.calls) == 2
