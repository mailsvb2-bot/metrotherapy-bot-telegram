from __future__ import annotations

import sqlite3
from pathlib import Path

from services import growth_autopilot


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
        CREATE TABLE events(name TEXT, user_id INTEGER, created_at TEXT);
        CREATE TABLE demo_events(user_id INTEGER, sent_at_utc TEXT, ack_at_utc TEXT);
        CREATE TABLE users(user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT);
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


def _patch_snapshot_dependencies(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(growth_autopilot, "db", lambda: _fake_db(path))
    monkeypatch.setattr(growth_autopilot, "_period_start", lambda period: "2026-05-10T00:00:00+00:00")
    monkeypatch.setattr(growth_autopilot, "_safe_segments", lambda: {})
    monkeypatch.setattr(growth_autopilot, "_safe_funnel2", lambda: {})


def test_snapshot_counts_distinct_paid_users_separately_from_payment_rows(tmp_path, monkeypatch):
    path = tmp_path / "growth_autopilot.db"
    with _fake_db(path) as conn:
        _setup(conn)
        conn.execute("INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)", (101, "buyer", "Анна"))
        conn.execute("INSERT INTO subscriptions(user_id, status, scope, plan_type) VALUES(?,?,?,?)", (101, "active", "morning", "5"))
        conn.execute(
            "INSERT INTO payments(user_id, amount, currency, created_at, provider_status) VALUES(?,?,?,?,?)",
            (101, 350000, "RUB", "2026-05-10T10:00:00+00:00", "succeeded"),
        )
        conn.execute(
            "INSERT INTO payments(user_id, amount, currency, created_at, provider_status) VALUES(?,?,?,?,?)",
            (101, 790000, "RUB", "2026-05-10T11:00:00+00:00", "succeeded"),
        )

    _patch_snapshot_dependencies(monkeypatch, path)

    snapshot = growth_autopilot.build_growth_autopilot_snapshot("today")

    assert snapshot["payments"]["payments"] == 2
    assert snapshot["payments"]["paid_users"] == 1
    assert snapshot["funnel"]["paid_users"] == 1


def test_snapshot_counts_redirect_clicks_as_total_events(tmp_path, monkeypatch):
    path = tmp_path / "growth_autopilot_clicks.db"
    with _fake_db(path) as conn:
        _setup(conn)
        conn.execute(
            "INSERT INTO events(name, user_id, created_at) VALUES(?,?,?)",
            ("ad_click_redirect", 0, "2026-05-10T10:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO events(name, user_id, created_at) VALUES(?,?,?)",
            ("ad_click_redirect", 0, "2026-05-10T10:01:00+00:00"),
        )
        conn.execute(
            "INSERT INTO events(name, user_id, created_at) VALUES(?,?,?)",
            ("funnel_start_command", 101, "2026-05-10T10:02:00+00:00"),
        )

    _patch_snapshot_dependencies(monkeypatch, path)

    snapshot = growth_autopilot.build_growth_autopilot_snapshot("today")

    assert snapshot["funnel"]["ad_clicks"] == 2
    assert snapshot["funnel"]["start_users"] == 1
    assert snapshot["funnel"]["click_to_start_pct"] == 50.0


def test_snapshot_keeps_ad_link_evidence_inside_selected_period(tmp_path, monkeypatch):
    path = tmp_path / "growth_autopilot_links.db"
    with _fake_db(path) as conn:
        _setup(conn)
        conn.execute(
            "INSERT INTO admin_ad_links(source, campaign, creative, ad_spend, start_payload, url, created_at) VALUES(?,?,?,?,?,?,?)",
            ("telegram_ads", "current", "creative_a", "340rub", "p1", "https://t.me/bot?start=p1", "2026-05-10T10:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO admin_ad_links(source, campaign, creative, ad_spend, start_payload, url, created_at) VALUES(?,?,?,?,?,?,?)",
            ("partner", "old", "creative_b", "999rub", "p2", "https://t.me/bot?start=p2", "2026-05-01T10:00:00+00:00"),
        )

    _patch_snapshot_dependencies(monkeypatch, path)

    snapshot = growth_autopilot.build_growth_autopilot_snapshot("today")

    assert snapshot["ad_links"]["links"] == 1
    assert snapshot["ad_links"]["spend_minor_low_confidence"] == 34000
    assert snapshot["ad_links"]["latest"][0]["campaign"] == "current"


def test_snapshot_keeps_access_alerts_inside_selected_period(tmp_path, monkeypatch):
    path = tmp_path / "growth_autopilot_alerts.db"
    with _fake_db(path) as conn:
        _setup(conn)
        conn.execute("INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)", (201, "old", "Олег"))
        conn.execute("INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)", (202, "new", "Ирина"))
        conn.execute(
            "INSERT INTO payments(user_id, amount, currency, created_at, provider_status) VALUES(?,?,?,?,?)",
            (201, 350000, "RUB", "2026-05-01T11:00:00+00:00", "succeeded"),
        )
        conn.execute(
            "INSERT INTO payments(user_id, amount, currency, created_at, provider_status) VALUES(?,?,?,?,?)",
            (202, 790000, "RUB", "2026-05-10T11:00:00+00:00", "succeeded"),
        )

    _patch_snapshot_dependencies(monkeypatch, path)

    snapshot = growth_autopilot.build_growth_autopilot_snapshot("today")

    assert snapshot["access_alerts"]["count"] == 1
    assert snapshot["access_alerts"]["rows"][0]["user_id"] == 202
