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


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE payments(
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
            problem TEXT
        )
        """
    )
    conn.commit()
    return conn


def test_yookassa_reconciliation_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "payments.db"

    def fake_db():
        return _DbCtx(_connect(db_path) if not db_path.exists() else sqlite3.connect(db_path))

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
    assert second.ok is True
    assert second.inserted is False


def test_yookassa_reconciliation_marks_missing_user(tmp_path, monkeypatch):
    db_path = tmp_path / "payments.db"

    def fake_db():
        return _DbCtx(_connect(db_path) if not db_path.exists() else sqlite3.connect(db_path))

    monkeypatch.setattr(reconciliation, "db", fake_db)
    monkeypatch.setattr(reconciliation, "tx", lambda conn: _Tx(conn))

    result = reconciliation.record_yookassa_webhook({
        "event": "payment.waiting_for_capture",
        "object": {"id": "provider-2", "status": "waiting_for_capture"},
    })

    assert result.ok is True
    assert result.problem == "missing_user_id"
    rows = reconciliation.payment_problem_summary()
    assert rows
    assert rows[0]["problem"] == "missing_user_id"
