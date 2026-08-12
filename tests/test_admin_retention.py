from __future__ import annotations

import pytest

from services.admin_retention import render_retention_money, retention_money_snapshot
from services.db import db


def _seed_retention_state(base: int, prefix: str) -> tuple[int, int, int, int]:
    low_id, zero_id, healthy_id, free_id = (base - 1, base - 2, base - 3, base - 4)
    user_ids = (low_id, zero_id, healthy_id, free_id)
    placeholders = ",".join("?" for _ in user_ids)

    with db() as conn:
        conn.execute(
            f"DELETE FROM payment_token_grants WHERE user_id IN ({placeholders})",
            user_ids,
        )
        conn.execute(
            f"DELETE FROM user_practice_preferences WHERE user_id IN ({placeholders})",
            user_ids,
        )
        conn.execute(
            f"DELETE FROM practice_wallets WHERE user_id IN ({placeholders})",
            user_ids,
        )
        conn.execute(
            f"DELETE FROM users WHERE user_id IN ({placeholders})",
            user_ids,
        )
        conn.executemany(
            "INSERT INTO users(user_id, username, first_name) VALUES(?,?,?)",
            [
                (low_id, f"{prefix}_low", "Low"),
                (zero_id, None, f"{prefix} Zero"),
                (healthy_id, f"{prefix}_healthy", "Healthy"),
                (free_id, f"{prefix}_free", "Free"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO practice_wallets(
                user_id, available_tokens, reserved_tokens, used_tokens
            ) VALUES(?,?,?,?)
            """.strip(),
            [
                (low_id, 4, 0, 56),
                (zero_id, 0, 0, 60),
                (healthy_id, 10, 0, 50),
                (free_id, 0, 0, 0),
            ],
        )
        conn.executemany(
            "INSERT INTO user_practice_preferences(user_id, delivery_mode) VALUES(?,?)",
            [
                (low_id, "both"),
                (zero_id, "single_daily"),
                (healthy_id, "morning_only"),
                (free_id, "single_daily"),
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
                ("yookassa", f"{prefix}-low", low_id, "practice_60", 60, None, "2099-08-10T10:00:00+00:00"),
                ("telegram_stars", f"{prefix}-zero-a", zero_id, "practice_start_7", 7, None, "2099-07-01T10:00:00+00:00"),
                ("telegram_stars", f"{prefix}-zero-b", zero_id, "practice_60", 60, None, "2099-08-11T10:00:00+00:00"),
                ("yookassa", f"{prefix}-healthy", healthy_id, "practice_60", 60, None, "2099-08-09T10:00:00+00:00"),
            ],
        )
        conn.commit()
    return user_ids


def test_retention_money_snapshot_uses_paid_grants_and_delivery_thresholds() -> None:
    baseline = retention_money_snapshot(limit=20)
    low_id, zero_id, healthy_id, free_id = _seed_retention_state(-931_000, "retention_snapshot")

    snapshot = retention_money_snapshot(limit=20)

    assert snapshot["paying_users"] == baseline["paying_users"] + 3
    assert snapshot["repeat_buyers"] == baseline["repeat_buyers"] + 1
    assert snapshot["repeat_purchase_rate"] == pytest.approx(
        (baseline["repeat_buyers"] + 1) / (baseline["paying_users"] + 3) * 100.0
    )
    assert snapshot["positive_balance_users"] == baseline["positive_balance_users"] + 2
    assert snapshot["healthy_balance_users"] == baseline["healthy_balance_users"] + 1
    assert snapshot["low_balance_users"] == baseline["low_balance_users"] + 1
    assert snapshot["zero_balance_users"] == baseline["zero_balance_users"] + 1

    candidates = {int(item["user_id"]): item for item in snapshot["refill_candidates"]}
    assert candidates[low_id]["segment"] == "низкий баланс"
    assert candidates[low_id]["refill_threshold"] == 4
    assert candidates[low_id]["name"] == "@retention_snapshot_low"
    assert candidates[zero_id]["segment"] == "баланс 0"
    assert candidates[zero_id]["refill_threshold"] == 2
    assert candidates[zero_id]["name"] == "retention_snapshot Zero"
    assert healthy_id not in candidates
    assert free_id not in candidates


def test_render_retention_money_is_actionable_and_bounded() -> None:
    low_id, zero_id, _, _ = _seed_retention_state(-932_000, "retention_render")
    snapshot = retention_money_snapshot(limit=1)

    text = render_retention_money(snapshot)

    assert "💰 Retention / повторные деньги" in text
    assert f"Плативших за пакеты: {snapshot['paying_users']}" in text
    assert f"Низкий баланс: {snapshot['low_balance_users']}" in text
    assert f"Баланс 0 после покупки: {snapshot['zero_balance_users']}" in text
    assert "@retention_render_low" in text
    assert "retention_render Zero" not in text
    assert int(snapshot["refill_candidates"][0]["user_id"]) == low_id
    assert int(snapshot["refill_candidates"][0]["user_id"]) != zero_id
    assert "delivery-aware" in text
