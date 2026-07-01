from __future__ import annotations

import importlib


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("METRO_DB_PATH", str(tmp_path / "account_merge.db"))
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")

    modules = {}
    for name in [
        "core.paths",
        "services.db.core",
        "services.schema_core",
        "services.schema",
        "services.accounts.identity",
        "services.accounts.audio_progress",
        "services.accounts.merge",
        "services.messenger.preferences",
        "services.messenger.outbound",
    ]:
        module = importlib.import_module(name)
        modules[name] = importlib.reload(module)

    modules["services.schema"].init_db()
    return modules


def test_account_merge_unifies_identities_and_delivery_plan(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    merge = modules["services.accounts.merge"]
    prefs = modules["services.messenger.preferences"]
    outbound = modules["services.messenger.outbound"]

    prefs.record_channel_identity(10001, "telegram", "tg-10001", username="test-user")
    prefs.record_channel_identity(20002, "vk", "vk-20002")
    identity.link_channel_to_account(30003, "max", "max-30003", display_name="Test")

    plan = merge.build_account_merge_plan(10001, [20002, 30003])
    assert plan.target_account_id == 10001
    assert plan.source_account_ids == [20002, 30003]
    assert plan.legacy_identity_counts[10001] == 1
    assert plan.legacy_identity_counts[20002] == 1
    assert plan.account_identity_counts[30003] == 1

    merge.apply_account_merge(10001, [20002, 30003], reason="test")

    snapshot = identity.get_account_snapshot(10001)
    assert {row["platform"] for row in snapshot["identities"]} == {"telegram", "vk", "max"}

    legacy_snapshot = prefs.get_channel_snapshot(10001)
    assert {row["platform"] for row in legacy_snapshot["identities"]} == {"telegram", "vk"}

    delivery_plan = outbound.build_delivery_plan(10001, preferred_platform="vk")
    assert delivery_plan.external_user_id == "vk-20002"


def test_account_merge_keeps_highest_audio_progress_across_sources(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    audio = modules["services.accounts.audio_progress"]
    merge = modules["services.accounts.merge"]

    audio.mark_audio_completed(10001, 3, platform="telegram")
    audio.mark_audio_sent(20002, 4, platform="vk", external_user_id="vk-20002")
    audio.mark_audio_completed(30003, 6, platform="max")

    merge.apply_account_merge(10001, [20002, 30003], reason="test")

    state = audio.get_audio_state(10001)
    assert state.last_completed_audio_no == 6
    assert state.next_audio_no == 7
    assert audio.get_audio_state(20002).last_completed_audio_no == 0
    assert audio.get_audio_state(30003).last_completed_audio_no == 0


def test_account_merge_writes_evidence_log(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    merge = modules["services.accounts.merge"]
    db_core = importlib.import_module("services.db")

    identity.link_channel_to_account(10001, "telegram", "tg-10001")
    identity.link_channel_to_account(20002, "vk", "vk-20002")

    merge.apply_account_merge(10001, [20002], reason="test")

    with db_core.db() as conn:
        row = conn.execute(
            "SELECT target_account_id, source_account_id, mode, status, evidence_json FROM account_merge_log"
        ).fetchone()

    assert int(row["target_account_id"]) == 10001
    assert int(row["source_account_id"]) == 20002
    assert row["mode"] == "test"
    assert row["status"] == "applied"
    assert '"source_account_ids": [20002]' in row["evidence_json"]
