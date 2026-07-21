from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from services import practice_tokens, reward_tokens
from services.migrations import practice_reward_grants_v1


def _reward_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE practice_wallets(
            user_id INTEGER PRIMARY KEY,
            available_tokens INTEGER NOT NULL DEFAULT 0,
            reserved_tokens INTEGER NOT NULL DEFAULT 0,
            used_tokens INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE practice_ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL,
            package_id TEXT,
            provider TEXT,
            provider_payment_id TEXT,
            idempotency_key TEXT NOT NULL UNIQUE
        );
        CREATE TABLE practice_token_lots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_key TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            provider_payment_id TEXT NOT NULL DEFAULT '',
            package_id TEXT NOT NULL DEFAULT '',
            granted_tokens INTEGER NOT NULL,
            available_tokens INTEGER NOT NULL,
            reserved_tokens INTEGER NOT NULL DEFAULT 0,
            used_tokens INTEGER NOT NULL DEFAULT 0,
            refundable INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE referrals(
            referred_id INTEGER PRIMARY KEY,
            referrer_id INTEGER NOT NULL,
            reward_given INTEGER DEFAULT 0,
            reward_days INTEGER,
            paid_at TEXT,
            bonus_applied INTEGER DEFAULT 0,
            bonus_applied_at TEXT
        );
        CREATE TABLE bonus_grants(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            days INTEGER NOT NULL,
            source TEXT NOT NULL,
            related_user_id INTEGER,
            granted_at_utc TEXT NOT NULL,
            reward_key TEXT UNIQUE,
            tokens_granted INTEGER,
            provider TEXT NOT NULL DEFAULT '',
            provider_payment_id TEXT NOT NULL DEFAULT '',
            ledger_id INTEGER
        );
        """
    )
    return conn


def _install_reward_db(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection) -> None:
    @contextmanager
    def db_context() -> Iterator[sqlite3.Connection]:
        yield conn
        conn.commit()

    @contextmanager
    def tx_context(value: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        yield value

    def ensure_wallet(value: sqlite3.Connection, user_id: int) -> None:
        value.execute(
            "INSERT OR IGNORE INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,0,0,0)",
            (int(user_id),),
        )

    def get_wallet(value: sqlite3.Connection, user_id: int) -> SimpleNamespace:
        row = value.execute(
            "SELECT available_tokens, reserved_tokens, used_tokens FROM practice_wallets WHERE user_id=?",
            (int(user_id),),
        ).fetchone()
        return SimpleNamespace(
            user_id=int(user_id),
            available_tokens=int(row[0]),
            reserved_tokens=int(row[1]),
            used_tokens=int(row[2]),
        )

    def insert_ledger(value: sqlite3.Connection, **kwargs: Any) -> int:
        value.execute(
            """
            INSERT INTO practice_ledger(
                user_id, event_type, amount, balance_after, reason, source,
                package_id, provider, provider_payment_id, idempotency_key
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """.strip(),
            tuple(
                kwargs[key]
                for key in (
                    "user_id",
                    "event_type",
                    "amount",
                    "balance_after",
                    "reason",
                    "source",
                    "package_id",
                    "provider",
                    "provider_payment_id",
                    "idempotency_key",
                )
            ),
        )
        return int(value.execute("SELECT last_insert_rowid()").fetchone()[0])

    def create_lot(value: sqlite3.Connection, **kwargs: Any) -> int:
        value.execute(
            """
            INSERT OR IGNORE INTO practice_token_lots(
                lot_key, user_id, provider, provider_payment_id, package_id,
                granted_tokens, available_tokens, refundable
            ) VALUES(?,?,?,?,?,?,?,?)
            """.strip(),
            (
                kwargs["lot_key"],
                kwargs["user_id"],
                kwargs["provider"],
                kwargs["provider_payment_id"],
                kwargs["package_id"],
                kwargs["amount"],
                kwargs["amount"],
                1 if kwargs["refundable"] else 0,
            ),
        )
        return int(
            value.execute(
                "SELECT id FROM practice_token_lots WHERE lot_key=?",
                (kwargs["lot_key"],),
            ).fetchone()[0]
        )

    monkeypatch.setattr(reward_tokens, "db", db_context)
    monkeypatch.setattr(reward_tokens, "tx", tx_context)
    monkeypatch.setattr(reward_tokens, "canonical_practice_user_id", lambda user_id: int(user_id))
    monkeypatch.setattr(reward_tokens, "ensure_wallet", ensure_wallet)
    monkeypatch.setattr(reward_tokens, "get_wallet_in_conn", get_wallet)
    monkeypatch.setattr(reward_tokens, "insert_ledger", insert_ledger)
    monkeypatch.setattr(reward_tokens, "create_lot_in_conn", create_lot)
    monkeypatch.setattr(
        reward_tokens,
        "utc_now",
        lambda: datetime(2026, 7, 21, 12, tzinfo=UTC),
    )
    monkeypatch.setattr(reward_tokens.settings, "REF_BONUS_MONTH_DAYS", 30, raising=False)
    monkeypatch.setattr(reward_tokens.settings, "REF_BONUS_WEEK_DAYS", 7, raising=False)
    monkeypatch.setattr(reward_tokens.settings, "REF_MAX_BONUSES", 10, raising=False)


def test_paid_referral_reward_is_token_backed_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _reward_db()
    _install_reward_db(monkeypatch, conn)
    conn.execute("INSERT INTO referrals(referred_id, referrer_id) VALUES(2,1)")
    conn.commit()
    try:
        first = reward_tokens.grant_referral_reward_for_payment(
            referred_user_id=2,
            package_tokens=60,
            provider="telegram_stars",
            provider_payment_id="charge-1",
        )
        duplicate = reward_tokens.grant_referral_reward_for_payment(
            referred_user_id=2,
            package_tokens=60,
            provider="telegram_stars",
            provider_payment_id="charge-1-replay",
        )
        assert first is not None and first.inserted and first.tokens == 30
        assert duplicate is not None and not duplicate.inserted
        assert duplicate.reason == "already_granted"
        assert conn.execute(
            "SELECT available_tokens FROM practice_wallets WHERE user_id=1"
        ).fetchone()[0] == 30
        assert tuple(
            conn.execute(
                "SELECT reward_given, reward_days, bonus_applied FROM referrals WHERE referred_id=2"
            ).fetchone()
        ) == (1, 30, 1)
        reward = conn.execute(
            "SELECT reward_key,tokens_granted,ledger_id FROM bonus_grants"
        ).fetchone()
        assert reward[0] == "referral:2" and reward[1] == 30 and int(reward[2]) > 0
        assert conn.execute("SELECT COUNT(*) FROM practice_ledger").fetchone()[0] == 1
    finally:
        conn.close()


def test_referral_limit_is_serialized_on_referrer_wallet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _reward_db()
    _install_reward_db(monkeypatch, conn)
    monkeypatch.setattr(reward_tokens.settings, "REF_MAX_BONUSES", 1, raising=False)
    conn.executemany(
        "INSERT INTO referrals(referred_id, referrer_id) VALUES(?,1)",
        [(2,), (3,)],
    )
    conn.commit()
    try:
        first = reward_tokens.grant_referral_reward_for_payment(
            referred_user_id=2,
            package_tokens=7,
            provider="yookassa",
            provider_payment_id="pay-1",
        )
        second = reward_tokens.grant_referral_reward_for_payment(
            referred_user_id=3,
            package_tokens=7,
            provider="yookassa",
            provider_payment_id="pay-2",
        )
        assert first is not None and first.inserted
        assert second is not None and not second.inserted
        assert second.reason == "limit_reached"
        assert conn.execute(
            "SELECT available_tokens FROM practice_wallets WHERE user_id=1"
        ).fetchone()[0] == 7
        assert conn.execute(
            "SELECT COUNT(*) FROM bonus_grants WHERE reward_key<>''"
        ).fetchone()[0] == 1
    finally:
        conn.close()


def test_payment_wrapper_repairs_referral_only_for_real_payment_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wallet = SimpleNamespace(available_tokens=60)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        practice_tokens,
        "_grant_tokens_for_payment",
        lambda **_kwargs: (False, wallet, 10),
    )
    monkeypatch.setattr(
        reward_tokens,
        "grant_referral_reward_for_payment",
        lambda **kwargs: calls.append(kwargs),
    )

    assert practice_tokens.grant_tokens_for_payment(
        provider="telegram_stars",
        provider_payment_id="charge",
        user_id=7,
        package_id="practice_60",
    ) == (False, wallet, 10)
    assert calls[0]["referred_user_id"] == 7
    assert calls[0]["package_tokens"] == 60
    practice_tokens.grant_tokens_for_payment(
        provider="gift_claim",
        provider_payment_id="gift-token",
        user_id=8,
        package_id="practice_60",
    )
    assert len(calls) == 1


def _migration_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE schema_migrations(name TEXT PRIMARY KEY, applied_at_utc TEXT);
        CREATE TABLE practice_wallets(
            user_id INTEGER PRIMARY KEY,
            available_tokens INTEGER NOT NULL DEFAULT 0,
            reserved_tokens INTEGER NOT NULL DEFAULT 0,
            used_tokens INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE practice_ledger(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            amount INTEGER NOT NULL,
            balance_after INTEGER NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            package_id TEXT,
            provider TEXT,
            provider_payment_id TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE practice_token_lots(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_key TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            provider_payment_id TEXT NOT NULL DEFAULT '',
            package_id TEXT NOT NULL DEFAULT '',
            granted_tokens INTEGER NOT NULL,
            available_tokens INTEGER NOT NULL,
            reserved_tokens INTEGER NOT NULL DEFAULT 0,
            used_tokens INTEGER NOT NULL DEFAULT 0,
            refundable INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE bonus_grants(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            days INTEGER NOT NULL,
            source TEXT NOT NULL,
            related_user_id INTEGER,
            granted_at_utc TEXT NOT NULL
        );
        INSERT INTO bonus_grants(user_id, days, source, related_user_id, granted_at_utc)
        VALUES(11,7,'referral',22,'2026-07-20T00:00:00+00:00'),
              (11,5,'gift',NULL,'2026-07-19T00:00:00+00:00');
        """
    )
    return conn


def test_reward_migration_backfills_only_remaining_legacy_bonus_rows_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        practice_reward_grants_v1,
        "utc_now",
        lambda: datetime(2026, 7, 21, 12, tzinfo=UTC),
    )
    conn = _migration_db()
    try:
        practice_reward_grants_v1.apply(conn)
        conn.commit()
        assert conn.execute(
            "SELECT available_tokens FROM practice_wallets WHERE user_id=11"
        ).fetchone()[0] == 9
        rows = conn.execute(
            "SELECT reward_key,tokens_granted,ledger_id,reward_status FROM bonus_grants ORDER BY id"
        ).fetchall()
        assert rows[0][0] == "referral:22"
        assert rows[0][1] == 7
        assert int(rows[0][2]) > 0
        assert rows[0][3] == "active"
        assert rows[1][0].startswith("legacy_bonus:")
        assert rows[1][1] == 5
        assert int(rows[1][2]) > 0
        assert rows[1][3] == "active"
        assert conn.execute("SELECT SUM(amount) FROM practice_ledger").fetchone()[0] == 9
        assert conn.execute("SELECT SUM(available_tokens) FROM practice_token_lots").fetchone()[0] == 9
        practice_reward_grants_v1.apply(conn)
        conn.commit()
        assert conn.execute(
            "SELECT available_tokens FROM practice_wallets WHERE user_id=11"
        ).fetchone()[0] == 9
    finally:
        conn.close()
