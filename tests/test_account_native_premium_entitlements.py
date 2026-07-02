from __future__ import annotations

import importlib


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("METRO_DB_PATH", str(tmp_path / "account_native_premium_entitlements.db"))
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")
    monkeypatch.setenv("STRESS_VIDEO_COURSE_URL", "https://example.test/course")

    modules = {}
    for name in [
        "core.paths",
        "services.db.core",
        "services.schema_core",
        "services.schema",
        "services.accounts.identity",
        "services.premium_entitlements",
        "services.accounts.diagnostics",
    ]:
        module = importlib.import_module(name)
        modules[name] = importlib.reload(module)

    modules["services.schema"].init_db()
    return modules


def test_premium_entitlements_are_account_native_from_linked_external_id(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    premium = modules["services.premium_entitlements"]
    db_core = importlib.import_module("services.db")

    identity.link_channel_to_account(10001, "telegram", "10001", verified=True)
    identity.link_channel_to_account(10001, "vk", "20002", verified=True)
    identity.link_channel_to_account(10001, "max", "30003", verified=True)

    result = premium.grant_premium_entitlements_for_payment(
        user_id=20002,
        package_id="practice_personal_month",
        provider="yookassa",
        provider_payment_id="pay-account-native-premium-1",
        fallback_platform="vk",
    )

    assert result.video_granted is True
    assert result.consultation_granted is True
    assert result.consultation_request_created is True
    assert result.outbox_created == 4

    with db_core.db() as conn:
        entitlement_rows = conn.execute(
            "SELECT user_id, entitlement_type FROM premium_entitlements ORDER BY entitlement_type"
        ).fetchall()
        outbox_rows = conn.execute(
            "SELECT user_id, platform, external_user_id, delivery_kind FROM premium_delivery_outbox ORDER BY delivery_kind, platform"
        ).fetchall()
        consultation_rows = conn.execute(
            "SELECT user_id, platform, external_user_id, package_id FROM consultation_requests"
        ).fetchall()

    assert [int(row["user_id"]) for row in entitlement_rows] == [10001, 10001]
    assert {row["entitlement_type"] for row in entitlement_rows} == {"consultation_60m", "stress_video_course"}

    assert {int(row["user_id"]) for row in outbox_rows} == {10001}
    assert {row["platform"] for row in outbox_rows} == {"telegram", "vk", "max"}
    assert len(outbox_rows) == 4

    assert len(consultation_rows) == 1
    assert int(consultation_rows[0]["user_id"]) == 10001
    assert consultation_rows[0]["package_id"] == "practice_personal_month"


def test_account_diagnostics_reports_account_native_premium_layer(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    premium = modules["services.premium_entitlements"]
    diagnostics = modules["services.accounts.diagnostics"]

    identity.link_channel_to_account(10001, "telegram", "10001", verified=True)
    identity.link_channel_to_account(10001, "vk", "20002", verified=True)
    identity.link_channel_to_account(10001, "max", "30003", verified=True)

    premium.grant_premium_entitlements_for_payment(
        user_id=30003,
        package_id="practice_personal_month",
        provider="yookassa",
        provider_payment_id="pay-account-native-premium-diag",
        fallback_platform="max",
    )

    payload = diagnostics.build_account_diagnostics(10001)

    assert payload["linked_user_ids"] == [10001, 20002, 30003]
    assert {row["entitlement_type"] for row in payload["premium_entitlements"]} == {
        "consultation_60m",
        "stress_video_course",
    }
    assert {int(row["user_id"]) for row in payload["premium_entitlements"]} == {10001}
    assert len(payload["consultation_requests"]) == 1
    assert int(payload["consultation_requests"][0]["user_id"]) == 10001
    assert len(payload["premium_delivery_outbox_tail"]) == 4
