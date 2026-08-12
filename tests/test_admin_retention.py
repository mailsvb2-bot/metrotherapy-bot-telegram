from __future__ import annotations

from services.admin_retention import render_retention_money, retention_money_snapshot
from services.db import db


def _seed_retention_state() -> None:
    with db() as conn:
        conn.executemany(
            "INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)",
            [
                (101, "low_user", "Low"),
                (102, None, "Zero User"),
                (103, "healthy_user", "Healthy"),
                (104, "free_user", "Free"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO practice_wallets(
                user_id, available_tokens, reserved_tokens, used_tokens
            ) VALUES(?,?,?,?)
            """.strip(),
            [
                (101, 4, 0, 56),
                (102, 0, 0, 60),
                (103, 10, 0, 50),
                (104, 0, 0, 0),
            ],
        )
        conn.executemany(
            "INSERT INTO user_practice_preferences(user_id, delivery_mode) VALUES(?,?)",
            [
                (101, "both"),
                (102, "single_daily"),
                (103, "morning_only"),
                (104, "single_daily"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO payment_token_grants(
                provider, provider_payment_id, user_id, package_id,
                tokens_granted, ledger_id, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """.strip(),
            [
                ("yookassa", "pay-101", 101, "practice_60", 60, None, "2026-08-10T10:00:00+00:00"),
                ("telegram_stars", "pay-102-a", 102, "practice_start_7", 7, None, "2026-07-01T10:00:00+00:00"),
                ("telegram_stars", "pay-102-b", 102, "practice_60", 60, None, "2026-08-11T10:00:00+00:00"),
                ("yookassa", "pay-103", 103, "practice_60", 60, None, "2026-08-09T10:00:00+00:00"),
            ],
        )
        conn.commit()


def test_retention_money_snapshot_uses_paid_grants_and_delivery_thresholds() -> None:
    _seed_retention_state()

    snapshot = retention_money_snapshot(limit=10)

    assert snapshot["paying_users"] == 3
    assert snapshot["repeat_buyers"] == 1
    assert snapshot["repeat_purchase_rate"] == 100 / 3
    assert snapshot["positive_balance_users"] == 2
    assert snapshot["healthy_balance_users"] == 1
    assert snapshot["low_balance_users"] == 1
    assert snapshot["zero_balance_users"] == 1

    candidates = snapshot["refill_candidates"]
    assert [item["user_id"] for item in candidates] == [101, 102]
    assert candidates[0]["segment"] == "низкий баланс"
    assert candidates[0]["refill_threshold"] == 4
    assert candidates[0]["name"] == "@low_user"
    assert candidates[1]["segment"] == "баланс 0"
    assert candidates[1]["refill_threshold"] == 2
    assert candidates[1]["name"] == "Zero User"
    assert all(item["user_id"] != 104 for item in candidates)


def test_render_retention_money_is_actionable_and_bounded() -> None:
    _seed_retention_state()

    text = render_retention_money(retention_money_snapshot(limit=1))

    assert "💰 Retention / повторные деньги" in text
    assert "Плативших за пакеты: 3" in text
    assert "Повторно покупали: 1 (33.3%)" in text
    assert "Низкий баланс: 1" in text
    assert "Баланс 0 после покупки: 1" in text
    assert "@low_user" in text
    assert "Zero User" not in text
    assert "delivery-aware" in text
