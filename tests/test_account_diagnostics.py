from __future__ import annotations

import importlib


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("METRO_DB_PATH", str(tmp_path / "account_diagnostics.db"))
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")

    modules = {}
    for name in [
        "core.paths",
        "services.db.core",
        "services.schema_core",
        "services.schema",
        "services.accounts.identity",
        "services.accounts.audio_progress",
        "services.practice_tokens",
        "services.accounts.diagnostics",
    ]:
        module = importlib.import_module(name)
        modules[name] = importlib.reload(module)

    modules["services.schema"].init_db()
    return modules


def test_account_diagnostics_reports_linked_identity_audio_and_wallet(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    audio = modules["services.accounts.audio_progress"]
    practice = modules["services.practice_tokens"]
    diagnostics = modules["services.accounts.diagnostics"]

    identity.link_channel_to_account(10001, "telegram", "10001", verified=True)
    identity.link_channel_to_account(10001, "vk", "20002", verified=True)
    identity.link_channel_to_account(10001, "max", "30003", verified=True)

    audio.mark_audio_completed(10001, 2, platform="telegram")
    practice.grant_tokens(10001, package_id="practice_start_7", amount=7, idempotency_key="grant:diag")

    payload = diagnostics.build_account_diagnostics(10001)

    assert payload["account_id"] == 10001
    assert payload["platforms"] == ["max", "telegram", "vk"]
    assert payload["linked_user_ids"] == [10001, 20002, 30003]
    assert payload["account_audio_progress"][0]["last_completed_audio_no"] == 2
    assert payload["canonical_views"][0]["canonical_account_id"] == 10001
    assert payload["canonical_views"][0]["wallet"]["available_tokens"] == 7
    assert payload["warnings"] == []
    assert payload["ok"] is True


def test_account_diagnostics_warns_about_orphan_source_wallet(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    diagnostics = modules["services.accounts.diagnostics"]
    db_core = importlib.import_module("services.db")

    identity.link_channel_to_account(10001, "telegram", "10001", verified=True)
    identity.link_channel_to_account(10001, "max", "20002", verified=True)

    with db_core.db() as conn:
        conn.execute(
            "INSERT INTO practice_wallets(user_id, available_tokens, reserved_tokens, used_tokens) VALUES(?,?,?,?)",
            (20002, 5, 0, 0),
        )

    payload = diagnostics.build_account_diagnostics(10001)

    assert "legacy_source_wallet_has_available_tokens:user_id=20002:available=5" in payload["warnings"]
    assert "target_wallet_missing" in payload["warnings"]
    assert payload["ok"] is False
