from __future__ import annotations

import sqlite3

from services.payments import reconciliation


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        return False


class _DbCtx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.conn.commit()
        self.conn.close()
        return False


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            telegram_charge_id TEXT NOT NULL UNIQUE,
            provider_charge_id TEXT,
            payload TEXT,
            amount INTEGER,
            currency TEXT,
            created_at TEXT,
            provider_status TEXT,
            provider_event_id TEXT,
            provider_raw TEXT,
            reconciled_at TEXT,
            problem TEXT,
            processing_status TEXT DEFAULT 'received',
            granted_at_utc TEXT,
            side_effects_done_at_utc TEXT,
            notified_at_utc TEXT,
            processing_error TEXT
        )
        """
    )
    conn.commit()


def _connect(path):
    conn = sqlite3.connect(path)
    _ensure_schema(conn)
    return conn


def test_yookassa_reconciliation_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "payments.db"

    def fake_db():
        return _DbCtx(_connect(db_path))

    monkeypatch.setattr(reconciliation, "db", fake_db)
    monkeypatch.setattr(reconciliation, "tx", lambda conn: _Tx(conn))

    payload = {
        "event": "payment.succeeded",
        "object": {
            "id": "provider-1",
            "status": "succeeded",
            "amount": {"value": "990.00", "currency": "RUB"},
            "metadata": {"external_user_id": "123", "kind": "subscription"},
        },
    }

    first = reconciliation.record_yookassa_webhook(payload)
    second = reconciliation.record_yookassa_webhook(payload)

    assert first.ok is True
    assert first.inserted is True
    assert first.processing_status == "provider_succeeded"
    assert first.side_effects_done is True
    assert second.ok is True
    assert second.inserted is False
    assert second.processing_status == "provider_succeeded"
    assert second.side_effects_done is True


def test_yookassa_reconciliation_marks_missing_user(tmp_path, monkeypatch):
    db_path = tmp_path / "payments.db"

    def fake_db():
        return _DbCtx(_connect(db_path))

    monkeypatch.setattr(reconciliation, "db", fake_db)
    monkeypatch.setattr(reconciliation, "tx", lambda conn: _Tx(conn))

    result = reconciliation.record_yookassa_webhook({
        "event": "payment.waiting_for_capture",
        "object": {"id": "provider-2", "status": "waiting_for_capture"},
    })

    assert result.ok is True
    assert result.problem == "missing_user_id"
    assert result.processing_status == "action_required"
    assert result.side_effects_done is False
    rows = reconciliation.payment_problem_summary()
    assert rows
    assert rows[0]["problem"] == "missing_user_id"
    assert rows[0]["processing_status"] == "action_required"
