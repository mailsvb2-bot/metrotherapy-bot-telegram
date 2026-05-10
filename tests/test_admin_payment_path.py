from __future__ import annotations

import sqlite3
from pathlib import Path

from services import admin_payment_path


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
        self.conn.close()
        return False


def _fake_db(path: Path):
    return _DbCtx(path)


def _setup(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE users(
            user_id INTEGER PRIMARY KEY,
            joined_at TEXT,
            username TEXT,
            first_name TEXT
        );
        CREATE TABLE payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER,
            currency TEXT,
            created_at TEXT,
            provider_status TEXT
        );
        CREATE TABLE events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            meta TEXT,
            created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO users(user_id, joined_at, username, first_name) VALUES(?,?,?,?)",
        (101, "2026-05-10T10:00:00+00:00", "buyer", "Анна"),
    )
    events = [
        (101, "start", '{"utm_source":"telegram_ads","utm_campaign":"may","utm_creative":"creative_1","ad_spend":"340 RUB"}', "2026-05-10T10:00:00+00:00"),
        (101, "funnel_demo_open", "{}", "2026-05-10T10:05:00+00:00"),
        (101, "funnel_demo_ack", "{}", "2026-05-10T10:25:00+00:00"),
        (101, "funnel_offer_shown", "{}", "2026-05-10T10:30:00+00:00"),
        (101, "funnel_offer_pay_clicked", "{}", "2026-05-10T10:40:00+00:00"),
        (101, "funnel_pay_success", "{}", "2026-05-10T12:30:00+00:00"),
    ]
    conn.executemany("INSERT INTO events(user_id, name, meta, created_at) VALUES(?,?,?,?)", events)
    conn.execute(
        "INSERT INTO payments(user_id, amount, currency, created_at, provider_status) VALUES(?,?,?,?,?)",
        (101, 790000, "RUB", "2026-05-10T12:30:00+00:00", "succeeded"),
    )
    conn.commit()


def test_payment_path_report_shows_client_path_and_attribution(tmp_path, monkeypatch):
    path = tmp_path / "path.db"
    with _fake_db(path) as conn:
        _setup(conn)

    monkeypatch.setattr(admin_payment_path, "db", lambda: _fake_db(path))
    monkeypatch.setattr(admin_payment_path, "_period_start", lambda period: "2026-05-10T00:00:00+00:00")

    report = admin_payment_path.payment_path_report("today")
    text = admin_payment_path.format_payment_path_report(report)

    assert report["count"] == 1
    assert "Путь до оплаты" in text
    assert "telegram_ads" in text
    assert "creative_1" in text
    assert "340 RUB" in text
    assert "От /start до оплаты: 2 ч. 30 мин." in text
    assert "/start" in text
    assert "клик оплаты" in text
    assert "оплата" in text
