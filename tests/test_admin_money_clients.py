from __future__ import annotations

import sqlite3
from pathlib import Path

from services import admin_cards, admin_money_clients


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
            first_name TEXT,
            work_time TEXT,
            home_time TEXT
        );
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
        );
        CREATE TABLE subscriptions(
            user_id INTEGER PRIMARY KEY,
            scope TEXT,
            plan_type TEXT,
            total_morning INTEGER,
            total_evening INTEGER,
            used_morning INTEGER,
            used_evening INTEGER,
            status TEXT,
            started_at TEXT,
            paid_at TEXT
        );
        CREATE TABLE events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            meta TEXT,
            created_at TEXT
        );
        CREATE TABLE demo_events(user_id INTEGER, kind TEXT, sent_at_utc TEXT, ack_at_utc TEXT);
        CREATE TABLE weather_prefs(user_id INTEGER PRIMARY KEY, city TEXT, lat REAL, lon REAL, updated_at TEXT);
        CREATE TABLE referrals(referred_id INTEGER PRIMARY KEY, referrer_id INTEGER, joined_at TEXT, reward_given INTEGER, reward_days INTEGER);
        CREATE TABLE user_behavior(user_id INTEGER PRIMARY KEY, ema_delta_ms REAL, ema_absdev_ms REAL, profile TEXT, updated_at TEXT);
        CREATE TABLE micro_answers(user_id INTEGER, q_key TEXT, answer TEXT, ts TEXT);
        CREATE TABLE gift_codes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by INTEGER,
            recipient_id INTEGER,
            redeemed_by INTEGER,
            claimed_by INTEGER,
            created_at TEXT
        );
        CREATE TABLE user_audio_timeline(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            sequence_key TEXT,
            event_type TEXT,
            anchor INTEGER,
            title TEXT,
            platform TEXT,
            token TEXT,
            meta_json TEXT,
            created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO users(user_id, joined_at, username, first_name, work_time, home_time) VALUES(?,?,?,?,?,?)",
        (101, "2026-05-10T10:00:00+00:00", "buyer", "Анна", "08:30", "19:00"),
    )
    conn.execute(
        "INSERT INTO payments(user_id, telegram_charge_id, provider_charge_id, payload, amount, currency, created_at, provider_status, problem) VALUES(?,?,?,?,?,?,?,?,?)",
        (101, "tg-1", "yk-1", "both_20", 790000, "RUB", "2026-05-10T12:30:00+00:00", "succeeded", ""),
    )
    conn.execute(
        "INSERT INTO subscriptions(user_id, scope, plan_type, total_morning, total_evening, used_morning, used_evening, status, started_at, paid_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (101, "both", "20", 20, 20, 8, 9, "active", "2026-05-10T12:30:00+00:00", "2026-05-10T12:30:00+00:00"),
    )
    conn.execute(
        "INSERT INTO events(user_id, name, meta, created_at) VALUES(?,?,?,?)",
        (101, "start", '{"utm_source":"telegram_ads","utm_campaign":"may","utm_creative":"creative_1","ad_spend":"340 RUB"}', "2026-05-10T10:00:00+00:00"),
    )
    conn.execute("INSERT INTO referrals(referred_id, referrer_id, joined_at, reward_given, reward_days) VALUES(?,?,?,?,?)", (202, 101, "2026-05-10", 1, 1))
    conn.execute("INSERT INTO gift_codes(created_by, recipient_id, redeemed_by, claimed_by, created_at) VALUES(?,?,?,?,?)", (101, 303, 303, 303, "2026-05-10"))
    for i in range(3):
        conn.execute(
            "INSERT INTO user_audio_timeline(user_id, sequence_key, event_type, title, platform, created_at) VALUES(?,?,?,?,?,?)",
            (101, "both", "delivered", f"Аудио {i + 1}", "telegram", f"2026-05-10T13:0{i}:00+00:00"),
        )
    conn.commit()


def test_money_period_summary_and_format(tmp_path, monkeypatch):
    path = tmp_path / "money.db"
    with _fake_db(path) as conn:
        _setup(conn)

    monkeypatch.setattr(admin_money_clients, "db", lambda: _fake_db(path))
    monkeypatch.setattr(admin_money_clients, "_period_start", lambda period: "2026-05-10T00:00:00+00:00")

    summary = admin_money_clients.money_period_summary("today")
    text = admin_money_clients.format_money_period(summary)

    assert summary["count"] == 1
    assert summary["paid_users"] == 1
    assert summary["amount"] == 790000
    assert "Деньги и клиенты" in text
    assert "Анна" in text
    assert "7900 RUB" in text


def test_payment_client_card_contains_business_fields(tmp_path, monkeypatch):
    path = tmp_path / "money.db"
    with _fake_db(path) as conn:
        _setup(conn)

    monkeypatch.setattr(admin_money_clients, "db", lambda: _fake_db(path))
    monkeypatch.setattr(admin_cards, "db", lambda: _fake_db(path))

    card = admin_money_clients.payment_client_card(1)
    text = admin_money_clients.format_payment_client_card(card)

    assert card["ok"] is True
    assert "telegram_ads" in text
    assert "creative_1" in text
    assert "340 RUB" in text
    assert "От /start до оплаты: 2 ч. 30 мин." in text
    assert "Прослушано/выдано по подписке: 17/40" in text
    assert "Подарков создано: 1" in text
    assert "Приглашено по реферальной записи: 1" in text
    assert "Вероятность: высокая" in text
    assert "Что сделать:" in text
