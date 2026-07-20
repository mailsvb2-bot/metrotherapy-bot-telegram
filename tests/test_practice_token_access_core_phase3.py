from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from services import practice_tokens_access_core as access
from services.practice_tokens_wallet import PracticeAccessDecision, PracticeWallet


class DbContext:
    def __init__(self, conn: Any) -> None:
        self.conn = conn

    def __enter__(self) -> Any:
        return self.conn

    def __exit__(self, *_args: Any) -> None:
        return None


@contextmanager
def no_tx(conn: Any):
    yield conn


class Cursor:
    def __init__(self, *, row: Any = None, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class Conn:
    def __init__(self, handler: Callable[[str, Any], Cursor]) -> None:
        self.handler = handler
        self.calls: list[tuple[str, Any]] = []

    def execute(self, query: str, params: Any = None) -> Cursor:
        normalized = " ".join(query.split())
        self.calls.append((normalized, params))
        return self.handler(normalized, params)


def wallet(available: int = 2, reserved: int = 0, used: int = 0) -> PracticeWallet:
    return PracticeWallet(7, available, reserved, used)


def test_delivered_reservation_ids_primary_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "ensure_schema", lambda _conn: None)
    primary = Conn(lambda _q, _p: Cursor(rows=[{"reservation_id": "r1"}, {"reservation_id": "r2"}]))
    monkeypatch.setattr(access, "db", lambda: DbContext(primary))
    assert access._delivered_reservation_ids(7) == ["r1", "r2"]

    calls = 0

    def fallback_handler(_query: str, _params: Any) -> Cursor:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("no such table: account_audio_progress")
        return Cursor(rows=[{"reservation_id": "legacy"}])

    fallback = Conn(fallback_handler)
    monkeypatch.setattr(access, "db", lambda: DbContext(fallback))
    assert access._delivered_reservation_ids(7) == ["legacy"]

    def other_error(_query: str, _params: Any) -> Cursor:
        raise sqlite3.OperationalError("database locked")

    monkeypatch.setattr(access, "db", lambda: DbContext(Conn(other_error)))
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        access._delivered_reservation_ids(7)


def test_reconcile_and_wallet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "canonical_practice_user_id", lambda uid: uid + 1)
    monkeypatch.setattr(access, "_delivered_reservation_ids", lambda uid: ["ok", "no"] if uid == 8 else [])
    consumed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        access,
        "consume_reservation",
        lambda rid, reason: consumed.append((rid, reason)) or rid == "ok",
    )
    assert access.reconcile_delivered_reservations(7) == 1
    assert consumed == [
        ("ok", "audio_delivery_reconciled"),
        ("no", "audio_delivery_reconciled"),
    ]

    conn = object()
    monkeypatch.setattr(access, "reconcile_delivered_reservations", lambda uid: 0)
    monkeypatch.setattr(access, "db", lambda: DbContext(conn))
    ensured: list[Any] = []
    monkeypatch.setattr(access, "ensure_wallet", lambda c, uid: ensured.append((c, uid)))
    monkeypatch.setattr(access, "get_wallet_in_conn", lambda c, uid: PracticeWallet(uid, 3, 1, 2))
    result = access.get_wallet(7)
    assert result == PracticeWallet(8, 3, 1, 2)
    assert ensured == [(conn, 8)]
    monkeypatch.setattr(access, "get_wallet", lambda _uid: wallet(0, 1))
    assert access.has_paid_practice_access(7) is True
    monkeypatch.setattr(access, "get_wallet", lambda _uid: wallet(0, 0))
    assert access.has_paid_practice_access(7) is False


def test_existing_reserved_queries() -> None:
    conn = Conn(lambda _q, _p: Cursor(row={"reservation_id": "r"}))
    assert access._existing_reserved(conn, user_id=7, session_id=8, audio_anchor=9)["reservation_id"] == "r"
    assert "session_id=?" in conn.calls[0][0]
    assert conn.calls[0][1] == (7, 8)

    conn = Conn(lambda _q, _p: Cursor(row={"reservation_id": "a"}))
    assert access._existing_reserved(conn, user_id=7, session_id=None, audio_anchor=9)["reservation_id"] == "a"
    assert "audio_anchor=?" in conn.calls[0][0]
    assert access._existing_reserved(conn, user_id=7, session_id=None, audio_anchor=None) is None


def patch_reserve_common(monkeypatch: pytest.MonkeyPatch, conn: Conn) -> None:
    monkeypatch.setattr(access, "canonical_practice_user_id", lambda uid: uid)
    monkeypatch.setattr(access.uuid, "uuid4", lambda: SimpleNamespace(hex="fixed"))
    monkeypatch.setattr(access, "db", lambda: DbContext(conn))
    monkeypatch.setattr(access, "tx", no_tx)
    monkeypatch.setattr(access, "ensure_wallet", lambda *_args: None)
    monkeypatch.setattr(access, "get_wallet_in_conn", lambda _c, uid: PracticeWallet(uid, 2, 0, 0))


def test_reserve_existing_and_insert_conflicts(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = Conn(lambda _q, _p: Cursor())
    patch_reserve_common(monkeypatch, conn)
    monkeypatch.setattr(access, "_existing_reserved", lambda *_args, **_kwargs: {"reservation_id": "existing"})
    assert access.reserve_practice(7, session_id=1) == (True, wallet(), "existing")

    existing_calls = iter([None, {"reservation_id": "winner"}])
    monkeypatch.setattr(access, "_existing_reserved", lambda *_args, **_kwargs: next(existing_calls))
    conflict = Conn(lambda query, _params: Cursor(rowcount=0) if query.startswith("INSERT OR IGNORE") else Cursor())
    patch_reserve_common(monkeypatch, conflict)
    assert access.reserve_practice(7, audio_anchor=3) == (True, wallet(), "winner")

    existing_calls = iter([None, None])
    monkeypatch.setattr(access, "_existing_reserved", lambda *_args, **_kwargs: next(existing_calls))
    conflict = Conn(lambda query, _params: Cursor(rowcount=0) if query.startswith("INSERT OR IGNORE") else Cursor())
    patch_reserve_common(monkeypatch, conflict)
    assert access.reserve_practice(7) == (False, wallet(), None)


def test_reserve_insufficient_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "_existing_reserved", lambda *_args, **_kwargs: None)
    responses = iter([Cursor(rowcount=1), Cursor(rowcount=0), Cursor(rowcount=1)])
    conn = Conn(lambda _q, _p: next(responses))
    patch_reserve_common(monkeypatch, conn)
    assert access.reserve_practice(7, session_id=1) == (False, wallet(), None)
    assert any(query.startswith("DELETE FROM practice_reservations") for query, _ in conn.calls)

    responses = iter([Cursor(rowcount=1), Cursor(rowcount=1)])
    conn = Conn(lambda _q, _p: next(responses))
    patch_reserve_common(monkeypatch, conn)
    lot_calls: list[Any] = []
    ledger_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(access, "reserve_from_lots", lambda *args, **kwargs: lot_calls.append((args, kwargs)))
    monkeypatch.setattr(access, "insert_ledger", lambda _conn, **kwargs: ledger_calls.append(kwargs))
    ok, after, reservation_id = access.reserve_practice(7, audio_anchor=4, reason="reason")
    assert ok is True
    assert after == wallet()
    assert reservation_id == "practice_res_fixed"
    assert lot_calls[0][1]["reservation_id"] == "practice_res_fixed"
    assert ledger_calls[0]["event_type"] == "reserve"
    assert ledger_calls[0]["idempotency_key"] == "reserve:practice_res_fixed"


def test_reservation_row() -> None:
    conn = Conn(lambda _q, _p: Cursor(row={"reservation_id": "r"}))
    assert access._reservation_row(conn, "r")["reservation_id"] == "r"
    assert conn.calls[0][1] == ("r",)


def patch_finalize_common(monkeypatch: pytest.MonkeyPatch, conn: Conn, rows: list[Any]) -> None:
    monkeypatch.setattr(access, "db", lambda: DbContext(conn))
    monkeypatch.setattr(access, "tx", no_tx)
    monkeypatch.setattr(access, "ensure_schema", lambda _conn: None)
    row_iter = iter(rows)
    monkeypatch.setattr(access, "_reservation_row", lambda _conn, _rid: next(row_iter))
    monkeypatch.setattr(access, "get_wallet_in_conn", lambda _conn, uid: PracticeWallet(uid, 2, 0, 1))


def test_consume_reservation_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    assert access.consume_reservation("") is False
    conn = Conn(lambda _q, _p: Cursor())
    patch_finalize_common(monkeypatch, conn, [None])
    assert access.consume_reservation("r") is False

    patch_finalize_common(monkeypatch, conn, [{"status": "consumed"}])
    assert access.consume_reservation("r") is True
    patch_finalize_common(monkeypatch, conn, [{"status": "released"}])
    assert access.consume_reservation("r") is False

    update_zero = Conn(lambda _q, _p: Cursor(rowcount=0))
    patch_finalize_common(monkeypatch, update_zero, [
        {"status": "reserved", "user_id": 7, "amount": 1},
        {"status": "consumed"},
    ])
    assert access.consume_reservation("r") is True

    responses = iter([Cursor(rowcount=1), Cursor(rowcount=1)])
    success = Conn(lambda _q, _p: next(responses))
    patch_finalize_common(monkeypatch, success, [{"status": "reserved", "user_id": 7, "amount": 1}])
    lot: list[str] = []
    ledger: list[dict[str, Any]] = []
    monkeypatch.setattr(access, "consume_lot_reservation", lambda _conn, rid: lot.append(rid))
    monkeypatch.setattr(access, "insert_ledger", lambda _conn, **kwargs: ledger.append(kwargs))
    assert access.consume_reservation("r", reason="done") is True
    assert lot == ["r"]
    assert ledger[0]["event_type"] == "consume"
    assert ledger[0]["reason"] == "done"


def test_release_reservation_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    assert access.release_reservation(" ") is False
    conn = Conn(lambda _q, _p: Cursor())
    patch_finalize_common(monkeypatch, conn, [None])
    assert access.release_reservation("r") is False
    patch_finalize_common(monkeypatch, conn, [{"status": "released"}])
    assert access.release_reservation("r") is True
    patch_finalize_common(monkeypatch, conn, [{"status": "consumed"}])
    assert access.release_reservation("r") is False

    update_zero = Conn(lambda _q, _p: Cursor(rowcount=0))
    patch_finalize_common(monkeypatch, update_zero, [
        {"status": "reserved", "user_id": 7, "amount": 1},
        {"status": "released"},
    ])
    assert access.release_reservation("r") is True

    responses = iter([Cursor(rowcount=1), Cursor(rowcount=1)])
    success = Conn(lambda _q, _p: next(responses))
    patch_finalize_common(monkeypatch, success, [{"status": "reserved", "user_id": 7, "amount": 1}])
    lot: list[str] = []
    ledger: list[dict[str, Any]] = []
    monkeypatch.setattr(access, "release_lot_reservation", lambda _conn, rid: lot.append(rid))
    monkeypatch.setattr(access, "insert_ledger", lambda _conn, **kwargs: ledger.append(kwargs))
    assert access.release_reservation("r", reason="failed") is True
    assert lot == ["r"]
    assert ledger[0]["event_type"] == "release"
    assert ledger[0]["amount"] == 1


def patch_access_check(monkeypatch: pytest.MonkeyPatch, *, mode: str, available: int, existing: Any = None) -> None:
    monkeypatch.setattr(access, "enforcement_mode", lambda: mode)
    monkeypatch.setattr(access, "token_economy_enabled", lambda: True)
    monkeypatch.setattr(access, "canonical_practice_user_id", lambda uid: uid)
    conn = object()
    monkeypatch.setattr(access, "db", lambda: DbContext(conn))
    monkeypatch.setattr(access, "ensure_wallet", lambda *_args: None)
    monkeypatch.setattr(access, "_existing_reserved", lambda *_args, **_kwargs: existing)
    monkeypatch.setattr(access, "get_wallet_in_conn", lambda _conn, uid: PracticeWallet(uid, available, 0, 0))


def test_check_and_reserve_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(access, "enforcement_mode", lambda: "hard")
    monkeypatch.setattr(access, "token_economy_enabled", lambda: True)
    assert access.check_and_reserve_for_audio(7, is_demo=True).reason == "free_demo_or_disabled"
    monkeypatch.setattr(access, "token_economy_enabled", lambda: False)
    assert access.check_and_reserve_for_audio(7, is_demo=False).reason == "free_demo_or_disabled"
    monkeypatch.setattr(access, "enforcement_mode", lambda: "off")
    assert access.check_and_reserve_for_audio(7, is_demo=False).allowed is True

    patch_access_check(monkeypatch, mode="hard", available=0, existing={"reservation_id": "existing"})
    decision = access.check_and_reserve_for_audio(7, is_demo=False, session_id=1)
    assert decision.reason == "existing_reservation"
    assert decision.reservation_id == "existing"

    patch_access_check(monkeypatch, mode="soft", available=0)
    decision = access.check_and_reserve_for_audio(7, is_demo=False)
    assert decision.allowed is True
    assert decision.reason == "soft_insufficient_balance"
    assert decision.warning == access.EMPTY_BALANCE_MESSAGE

    patch_access_check(monkeypatch, mode="hard", available=0)
    decision = access.check_and_reserve_for_audio(7, is_demo=False)
    assert decision.allowed is False
    assert decision.reason == "insufficient_balance"

    patch_access_check(monkeypatch, mode="soft", available=1)
    monkeypatch.setattr(access, "reserve_practice", lambda *_args, **_kwargs: (False, wallet(), None))
    decision = access.check_and_reserve_for_audio(7, is_demo=False)
    assert decision.allowed is True
    assert decision.reason == "soft_reserve_failed"

    patch_access_check(monkeypatch, mode="hard", available=1)
    monkeypatch.setattr(access, "reserve_practice", lambda *_args, **_kwargs: (False, wallet(), None))
    decision = access.check_and_reserve_for_audio(7, is_demo=False)
    assert decision.allowed is False
    assert decision.reason == "reserve_failed"

    patch_access_check(monkeypatch, mode="hard", available=1)
    monkeypatch.setattr(access, "reserve_practice", lambda *_args, **_kwargs: (True, wallet(), "r"))
    decision = access.check_and_reserve_for_audio(7, is_demo=False, audio_anchor=2)
    assert decision == PracticeAccessDecision(True, "hard", "reserved", reservation_id="r")


def test_finalize_audio_access(monkeypatch: pytest.MonkeyPatch) -> None:
    assert access.finalize_audio_access(PracticeAccessDecision(True, "hard", "free"), delivered=True) is True
    consumed: list[str] = []
    released: list[str] = []
    monkeypatch.setattr(access, "consume_reservation", lambda rid: consumed.append(rid) or True)
    monkeypatch.setattr(access, "release_reservation", lambda rid: released.append(rid) or True)
    decision = PracticeAccessDecision(True, "hard", "reserved", reservation_id="r")
    assert access.finalize_audio_access(decision, delivered=True) is True
    assert access.finalize_audio_access(decision, delivered=False) is True
    assert consumed == ["r"]
    assert released == ["r"]
