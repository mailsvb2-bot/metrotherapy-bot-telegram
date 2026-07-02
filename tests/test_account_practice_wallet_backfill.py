from __future__ import annotations

import importlib


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("METRO_DB_PATH", str(tmp_path / "account_practice_wallet_backfill.db"))
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")

    modules = {}
    for name in [
        "core.paths",
        "services.db.core",
        "services.schema_core",
        "services.schema",
        "services.accounts.identity",
        "services.practice_tokens",
        "services.accounts.practice_wallet_backfill",
    ]:
        module = importlib.import_module(name)
        modules[name] = importlib.reload(module)

    modules["services.schema"].init_db()
    return modules


def test_practice_wallet_backfill_dry_run_adds_source_available_to_target(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    db_core = importlib.import_module("services.db")
    backfill = modules["services.accounts.practice_wallet_backfill"]

    with db_core.db() as conn:
        conn.execute(
            "INSERT INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)",
            (10001, 2, 0, 0),
        )
        conn.execute(
            "INSERT INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)",
            (20002, 5, 0, 0),
        )
        conn.execute(
            "INSERT INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)",
            (30003, 7, 0, 0),
        )

    plan = backfill.build_account_practice_wallet_backfill_plan(10001, [10001, 20002, 30003])

    assert plan.existing_available_tokens == 2
    assert plan.source_available_tokens == 12
    assert plan.planned_available_tokens == 14
    assert plan.already_applied is False


def test_practice_wallet_backfill_apply_is_idempotent_and_keeps_source_rows(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    db_core = importlib.import_module("services.db")
    practice = modules["services.practice_tokens"]
    backfill = modules["services.accounts.practice_wallet_backfill"]

    with db_core.db() as conn:
        conn.execute(
            "INSERT INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)",
            (10001, 2, 0, 0),
        )
        conn.execute(
            "INSERT INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)",
            (20002, 5, 0, 0),
        )

    applied = backfill.apply_account_practice_wallet_backfill(10001, [10001, 20002])
    assert applied.planned_available_tokens == 7
    assert practice.get_wallet(10001).available_tokens == 7

    applied_again = backfill.apply_account_practice_wallet_backfill(10001, [10001, 20002])
    assert applied_again.already_applied is True
    assert applied_again.planned_available_tokens == 7
    assert practice.get_wallet(10001).available_tokens == 7

    with db_core.db() as conn:
        source = conn.execute(
            "SELECT available_tokens FROM practice_wallets WHERE user_id=?",
            (20002,),
        ).fetchone()
        ledger_rows = conn.execute(
            "SELECT event_type, amount, balance_after FROM practice_ledger WHERE user_id=?",
            (10001,),
        ).fetchall()

    assert int(source["available_tokens"]) == 5
    assert [dict(row) for row in ledger_rows] == [
        {"event_type": "account_wallet_backfill", "amount": 5, "balance_after": 7}
    ]


def test_practice_wallet_backfill_reports_non_available_source_counters(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    db_core = importlib.import_module("services.db")
    backfill = modules["services.accounts.practice_wallet_backfill"]

    with db_core.db() as conn:
        conn.execute(
            "INSERT INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)",
            (20002, 5, 1, 2),
        )

    plan = backfill.build_account_practice_wallet_backfill_plan(10001, [20002])

    assert plan.source_available_tokens == 5
    assert plan.source_reserved_tokens == 1
    assert plan.source_used_tokens == 2
    assert "source_reserved_tokens_are_not_transferred" in plan.warnings
    assert "source_used_tokens_are_reported_only" in plan.warnings
