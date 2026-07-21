from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from services import gifts


class Cursor:
    def __init__(self, rowcount: int = 0) -> None:
        self.rowcount = rowcount


class Connection:
    def __init__(
        self,
        *,
        rows: list[Any] | None = None,
        rowcounts: list[int] | None = None,
        execute_error: BaseException | None = None,
    ) -> None:
        self.rows = list(rows or [])
        self.rowcounts = list(rowcounts or [])
        self.execute_error = execute_error
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._last_row: Any = None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Connection | Cursor:
        self.calls.append((sql, params))
        if self.execute_error is not None:
            raise self.execute_error
        if sql.lstrip().upper().startswith("SELECT"):
            self._last_row = self.rows.pop(0) if self.rows else None
            return self
        return Cursor(self.rowcounts.pop(0) if self.rowcounts else 0)

    def fetchone(self) -> Any:
        return self._last_row


@contextmanager
def connection_context(conn: Connection) -> Iterator[Connection]:
    yield conn


def use_connection(monkeypatch: pytest.MonkeyPatch, conn: Connection) -> None:
    monkeypatch.setattr(gifts, "db", lambda: connection_context(conn))


def gift_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "plan_id": 3,
        "scope": "full",
        "days": 30,
        "created_by": 11,
        "recipient_id": None,
        "status": "created",
        "paid": 1,
        "expires_at": None,
        "redeemed_by": None,
        "claimed_by": None,
    }
    row.update(overrides)
    return row


def test_gift_payload_normalizes_values() -> None:
    assert gifts._gift_payload(gift_row(plan_id="4", days="7", created_by="5")) == {
        "plan_id": 4,
        "scope": "full",
        "days": 7,
        "created_by": 5,
        "recipient_id": None,
        "status": "created",
    }
    assert gifts._gift_payload(gift_row(plan_id="bad", days=None, created_by=None, status=None)) == {
        "plan_id": None,
        "scope": "full",
        "days": 0,
        "created_by": 0,
        "recipient_id": None,
        "status": "created",
    }


def test_create_gift_validates_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gifts, "get_plan_by_id", lambda _plan_id: None)
    with pytest.raises(ValueError, match="unknown plan_id"):
        gifts.create_gift(99, 1)

    monkeypatch.setattr(gifts, "get_plan_by_id", lambda _plan_id: {"scope": "", "days": 0})
    with pytest.raises(ValueError, match="invalid plan"):
        gifts.create_gift(1, 1)


@pytest.mark.parametrize("recipient_id", [None, 22])
def test_create_gift_inserts_canonical_plan_fields(
    monkeypatch: pytest.MonkeyPatch,
    recipient_id: int | None,
) -> None:
    conn = Connection(rowcounts=[1])
    use_connection(monkeypatch, conn)
    monkeypatch.setattr(gifts, "get_plan_by_id", lambda _plan_id: {"scope": "full", "days": 30})
    monkeypatch.setattr(gifts.secrets, "token_urlsafe", lambda _n: "gift-code_1")
    monkeypatch.setattr(
        gifts,
        "utc_now",
        lambda: gifts.datetime.fromisoformat("2026-07-21T00:00:00+00:00"),
    )

    code = gifts.create_gift(3, 11, recipient_id)

    assert code == "giftcode1"
    sql, params = conn.calls[0]
    assert "INSERT INTO gift_codes" in sql
    assert params[:6] == ("giftcode1", 3, "full", 30, 11, recipient_id)
    assert params[-1] == 0


def test_create_gift_retries_collisions_then_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = Connection(execute_error=sqlite3.IntegrityError("duplicate"))
    use_connection(monkeypatch, conn)
    monkeypatch.setattr(gifts, "get_plan_by_id", lambda _plan_id: {"scope": "full", "days": 30})
    monkeypatch.setattr(gifts.secrets, "token_urlsafe", lambda _n: "duplicate")

    with pytest.raises(RuntimeError, match="failed to generate unique gift code"):
        gifts.create_gift(3, 11)
    assert len(conn.calls) == 8


def test_mark_gift_paid_transaction_and_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = Connection(rowcounts=[1, 0])
    assert gifts.mark_gift_paid_tx(conn, "code", payment_id="pay") is True
    assert gifts.mark_gift_paid_tx(conn, "code") is False
    assert conn.calls[0][1] == ("pay", "code")
    assert conn.calls[1][1] == (None, "code")

    wrapped = Connection(rowcounts=[1])
    use_connection(monkeypatch, wrapped)
    assert gifts.mark_gift_paid("code", "payment") is True


class BadExpiryRow(dict[str, Any]):
    def __getitem__(self, key: str) -> Any:
        if key == "expires_at":
            raise TypeError("bad expiry")
        return super().__getitem__(key)


def test_get_gift_status_all_states(monkeypatch: pytest.MonkeyPatch) -> None:
    now = gifts.datetime.fromisoformat("2026-07-21T00:00:00+00:00")
    monkeypatch.setattr(gifts, "utc_now", lambda: now)

    cases = [
        (None, False, "не найден", None),
        (gift_row(expires_at="2026-07-20T23:59:59+00:00"), False, "истёк", None),
        (gift_row(paid=0), False, "не оплачен", None),
        (gift_row(status="activated"), False, "уже активирован", True),
        (gift_row(redeemed_by=55), False, "уже активирован", True),
        (gift_row(expires_at="2026-07-22T00:00:00+00:00"), True, "OK", True),
    ]
    for row, expected_ok, text, has_payload in cases:
        conn = Connection(rows=[row])
        use_connection(monkeypatch, conn)
        ok, message, payload = gifts.get_gift_status("code")
        assert ok is expected_ok
        assert text in message
        assert (payload is not None) is bool(has_payload)

    bad_row = BadExpiryRow(gift_row(expires_at=object()))
    conn = Connection(rows=[bad_row])
    use_connection(monkeypatch, conn)
    ok, message, payload = gifts.get_gift_status("code")
    assert ok is True
    assert message == "OK"
    assert payload is not None


def test_redeem_gift_rejects_invalid_states(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [
        (None, "не найден"),
        (gift_row(paid=0), "не оплачен"),
        (gift_row(recipient_id=99), "другому пользователю"),
        (gift_row(status="activated"), "уже активирован"),
        (gift_row(redeemed_by=55), "уже активирован"),
    ]
    for row, text in cases:
        conn = Connection(rows=[row])
        use_connection(monkeypatch, conn)
        ok, message, payload = gifts.redeem_gift("code", 7)
        assert ok is False
        assert text in message
        assert payload is None or text == "уже активирован"


def test_redeem_gift_idempotent_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    already = gift_row(status="claimed", claimed_by=7)
    conn = Connection(rows=[already])
    use_connection(monkeypatch, conn)
    ok, message, payload = gifts.redeem_gift("code", 7)
    assert ok is True
    assert "уже принят" in message
    assert payload is not None

    created = gift_row()
    claimed = gift_row(status="claimed", claimed_by=7)
    conn = Connection(rows=[created, claimed], rowcounts=[1])
    use_connection(monkeypatch, conn)
    ok, message, payload = gifts.redeem_gift("code", 7)
    assert ok is True
    assert message == "✅ Подарок принят."
    assert payload is not None
    assert "UPDATE gift_codes SET status='claimed'" in conn.calls[1][0]


def test_redeem_gift_concurrent_claim_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    created = gift_row()
    same_user = gift_row(status="claimed", claimed_by=7)
    conn = Connection(rows=[created, same_user], rowcounts=[0])
    use_connection(monkeypatch, conn)
    ok, message, payload = gifts.redeem_gift("code", 7)
    assert ok is True
    assert "уже принят" in message
    assert payload is not None

    other_user = gift_row(status="claimed", claimed_by=8)
    conn = Connection(rows=[created, other_user], rowcounts=[0])
    use_connection(monkeypatch, conn)
    ok, message, payload = gifts.redeem_gift("code", 7)
    assert ok is False
    assert "другим пользователем" in message
    assert payload is None


def test_activate_gift_all_states(monkeypatch: pytest.MonkeyPatch) -> None:
    for row in (None, gift_row(paid=0), gift_row(status="claimed", claimed_by=8)):
        conn = Connection(rows=[row])
        use_connection(monkeypatch, conn)
        assert gifts.activate_gift("code", 7) is False

    conn = Connection(rows=[gift_row(status="claimed", claimed_by=7)], rowcounts=[1])
    use_connection(monkeypatch, conn)
    assert gifts.activate_gift("code", 7) is True
    assert conn.calls[-1][1][1] == 7

    conn = Connection(rows=[gift_row(status="claimed", claimed_by=None)], rowcounts=[0])
    use_connection(monkeypatch, conn)
    assert gifts.activate_gift("code", 7) is False
