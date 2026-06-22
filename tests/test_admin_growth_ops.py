from __future__ import annotations

import sqlite3
from pathlib import Path

from services import admin_growth_ops


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
        CREATE TABLE users(user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, joined_at TEXT);
        CREATE TABLE payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            currency TEXT,
            created_at TEXT,
            provider_status TEXT
        );
        CREATE TABLE subscriptions(user_id INTEGER, status TEXT, scope TEXT, plan_type TEXT);
        CREATE TABLE admin_ad_links(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            campaign TEXT,
            creative TEXT,
            ad_spend TEXT,
            start_payload TEXT,
            url TEXT,
            created_at TEXT
        );
        """
    )
    conn.execute("INSERT INTO users(user_id, username, first_name, joined_at) VALUES(?,?,?,?)", (101, "buyer", "Анна", "2026-05-10T10:00:00+00:00"))
    conn.execute("INSERT INTO users(user_id, username, first_name, joined_at) VALUES(?,?,?,?)", (102, "active", "Иван", "2026-05-10T11:00:00+00:00"))
    conn.execute("INSERT INTO payments(user_id, amount, currency, created_at, provider_status) VALUES(?,?,?,?,?)", (101, 790000, "RUB", "2026-05-10T12:30:00+00:00", "succeeded"))
    conn.execute("INSERT INTO payments(user_id, amount, currency, created_at, provider_status) VALUES(?,?,?,?,?)", (102, 350000, "RUB", "2026-05-10T12:40:00+00:00", "succeeded"))
    conn.execute("INSERT INTO subscriptions(user_id, status, scope, plan_type) VALUES(?,?,?,?)", (102, "active", "morning", "5"))
    conn.execute("INSERT INTO admin_ad_links(source, campaign, creative, ad_spend, start_payload, url, created_at) VALUES(?,?,?,?,?,?,?)", ("telegram_ads", "may", "reels1", "340rub", "p1", "https://t.me/bot?start=p1", "2026-05-10T10:00:00+00:00"))
    conn.execute("INSERT INTO admin_ad_links(source, campaign, creative, ad_spend, start_payload, url, created_at) VALUES(?,?,?,?,?,?,?)", ("partner", "may", "post1", "", "p2", "https://t.me/bot?start=p2", "2026-05-10T10:10:00+00:00"))


def test_ad_spend_summary_uses_existing_ad_links(tmp_path, monkeypatch):
    path = tmp_path / "growth.db"
    with _fake_db(path) as conn:
        _setup(conn)
    monkeypatch.setattr(admin_growth_ops, "db", lambda: _fake_db(path))

    text = admin_growth_ops.format_ad_spend_summary(admin_growth_ops.ad_spend_summary())

    assert "Расходы на рекламу" in text
    assert "340rub" in text
    assert "Ссылок без расхода: 1" in text


def test_access_alerts_find_paid_user_without_active_access(tmp_path, monkeypatch):
    path = tmp_path / "alerts.db"
    with _fake_db(path) as conn:
        _setup(conn)
    monkeypatch.setattr(admin_growth_ops, "db", lambda: _fake_db(path))

    rows = admin_growth_ops.access_alerts()
    text = admin_growth_ops.format_access_alerts(rows)

    assert len(rows) == 1
    assert rows[0]["user_id"] == 101
    assert "Деньги есть" in text
    assert "Анна" in text


def test_money_csv_exports_payments(tmp_path, monkeypatch):
    path = tmp_path / "csv.db"
    with _fake_db(path) as conn:
        _setup(conn)
    monkeypatch.setattr(admin_growth_ops, "db", lambda: _fake_db(path))

    csv_text = admin_growth_ops.money_csv("all")

    assert "payment_id,user_id,client,amount" in csv_text
    assert "Анна @buyer" in csv_text
    assert "7900.0" in csv_text
