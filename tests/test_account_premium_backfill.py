from __future__ import annotations

import importlib


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("METRO_DB_PATH", str(tmp_path / "account_premium_backfill.db"))
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")

    modules = {}
    for name in [
        "core.paths",
        "services.db.core",
        "services.schema_core",
        "services.schema",
        "services.accounts.identity",
        "services.premium_entitlements",
        "services.accounts.premium_backfill",
        "services.accounts.diagnostics",
    ]:
        module = importlib.import_module(name)
        modules[name] = importlib.reload(module)

    modules["services.schema"].init_db()
    return modules


def _insert_legacy_premium_rows(conn, legacy_user_id: int) -> None:
    conn.execute(
        """
        INSERT INTO premium_entitlements(
            user_id, package_id, entitlement_type, provider, provider_payment_id, source, status, idempotency_key
        ) VALUES(?,?,?,?,?,?,?,?)
        """.strip(),
        (
            legacy_user_id,
            "practice_antistress_60",
            "stress_video_course",
            "yookassa",
            "legacy-premium-payment",
            "yookassa_webhook",
            "active",
            "premium:yookassa:legacy-premium-payment:stress_video_course",
        ),
    )
    conn.execute(
        """
        INSERT INTO premium_delivery_outbox(
            user_id, platform, external_user_id, delivery_kind, title, body, status, idempotency_key
        ) VALUES(?,?,?,?,?,?,?,?)
        """.strip(),
        (
            legacy_user_id,
            "max",
            str(legacy_user_id),
            "video_course_access",
            "Video course access",
            "body",
            "pending",
            "premium_delivery:yookassa:legacy-premium-payment:video:max:legacy",
        ),
    )
    conn.execute(
        """
        INSERT INTO consultation_requests(
            user_id, platform, external_user_id, package_id, provider, provider_payment_id,
            status, contact_payload, admin_note, idempotency_key
        ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """.strip(),
        (
            legacy_user_id,
            "max",
            str(legacy_user_id),
            "practice_personal_month",
            "yookassa",
            "legacy-consultation-payment",
            "new",
            "contact",
            "note",
            "consultation:yookassa:legacy-consultation-payment",
        ),
    )


def test_account_premium_backfill_dry_run_and_apply_are_idempotent(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    backfill = modules["services.accounts.premium_backfill"]
    db_core = importlib.import_module("services.db")

    identity.link_channel_to_account(10001, "telegram", "10001", verified=True)
    identity.link_channel_to_account(10001, "max", "20002", verified=True)

    with db_core.db() as conn:
        _insert_legacy_premium_rows(conn, 20002)

    dry_run = backfill.build_account_premium_backfill_plan(10001)
    assert dry_run.total_rows == 3
    assert dry_run.already_applied is False
    assert dry_run.to_dict()["counts_by_table"] == {
        "premium_entitlements": 1,
        "premium_delivery_outbox": 1,
        "consultation_requests": 1,
    }

    applied = backfill.apply_account_premium_backfill(10001)
    assert applied.total_rows == 0
    assert applied.already_applied is True

    applied_again = backfill.apply_account_premium_backfill(10001)
    assert applied_again.total_rows == 0
    assert applied_again.already_applied is True

    with db_core.db() as conn:
        premium_user_ids = [int(row["user_id"]) for row in conn.execute("SELECT user_id FROM premium_entitlements").fetchall()]
        outbox_user_ids = [int(row["user_id"]) for row in conn.execute("SELECT user_id FROM premium_delivery_outbox").fetchall()]
        consultation_user_ids = [int(row["user_id"]) for row in conn.execute("SELECT user_id FROM consultation_requests").fetchall()]

    assert premium_user_ids == [10001]
    assert outbox_user_ids == [10001]
    assert consultation_user_ids == [10001]


def test_account_diagnostics_is_clean_after_premium_backfill(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    backfill = modules["services.accounts.premium_backfill"]
    diagnostics = modules["services.accounts.diagnostics"]
    db_core = importlib.import_module("services.db")

    identity.link_channel_to_account(10001, "telegram", "10001", verified=True)
    identity.link_channel_to_account(10001, "vk", "20002", verified=True)
    identity.link_channel_to_account(10001, "max", "30003", verified=True)

    with db_core.db() as conn:
        conn.execute(
            "INSERT INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)",
            (10001, 1, 0, 0),
        )
        _insert_legacy_premium_rows(conn, 30003)

    before = diagnostics.build_account_diagnostics(10001)
    assert {int(row["user_id"]) for row in before["premium_entitlements"]} == {30003}
    assert {int(row["user_id"]) for row in before["consultation_requests"]} == {30003}

    backfill.apply_account_premium_backfill(10001)

    after = diagnostics.build_account_diagnostics(10001)
    assert {int(row["user_id"]) for row in after["premium_entitlements"]} == {10001}
    assert {int(row["user_id"]) for row in after["premium_delivery_outbox_tail"]} == {10001}
    assert {int(row["user_id"]) for row in after["consultation_requests"]} == {10001}
