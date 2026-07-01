from __future__ import annotations

import importlib

import pytest


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("METRO_DB_PATH", str(tmp_path / "account_identity.db"))
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")

    modules = {}
    for name in [
        "core.paths",
        "services.db.core",
        "services.schema_core",
        "services.schema",
        "services.accounts.identity",
        "services.accounts.audio_progress",
        "services.messenger.bridge",
        "services.messenger.entrypoints",
        "services.messenger.preferences",
        "services.messenger.outbound",
    ]:
        module = importlib.import_module(name)
        modules[name] = importlib.reload(module)

    modules["services.schema"].init_db()
    return modules


def test_bridge_links_second_messenger_to_same_account(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    entrypoints = modules["services.messenger.entrypoints"]
    bridge = modules["services.messenger.bridge"]
    identity = modules["services.accounts.identity"]
    prefs = modules["services.messenger.preferences"]

    first = entrypoints.register_user_entry(
        10001,
        platform="telegram",
        external_user_id="tg-10001",
        username="test-user",
        display_name="Test User",
    )
    assert first.user_id == 10001

    token = bridge.issue_bridge_token(
        10001,
        target_platform="vk",
        created_from_platform="telegram",
        created_from_external_user_id="tg-10001",
    )
    linked = entrypoints.register_user_entry(
        20002,
        platform="vk",
        external_user_id="vk-20002",
        start_payload=f"bridge_{token}",
    )

    assert linked.user_id == 10001
    assert linked.linked_via_bridge is True

    snapshot = identity.get_account_snapshot(10001)
    assert {row["platform"] for row in snapshot["identities"]} == {"telegram", "vk"}

    legacy_snapshot = prefs.get_channel_snapshot(10001)
    assert {row["platform"] for row in legacy_snapshot["identities"]} == {"telegram", "vk"}


def test_plain_returning_messenger_resolves_existing_account(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    entrypoints = modules["services.messenger.entrypoints"]
    bridge = modules["services.messenger.bridge"]

    entrypoints.register_user_entry(10001, platform="telegram", external_user_id="tg-10001")
    token = bridge.issue_bridge_token(10001, target_platform="vk")
    entrypoints.register_user_entry(
        20002,
        platform="vk",
        external_user_id="vk-20002",
        start_payload=f"bridge_{token}",
    )

    returning = entrypoints.register_user_entry(
        20002,
        platform="vk",
        external_user_id="vk-20002",
    )

    assert returning.user_id == 10001
    assert returning.linked_via_bridge is False


def test_delivery_plan_uses_linked_channel_identity(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    entrypoints = modules["services.messenger.entrypoints"]
    bridge = modules["services.messenger.bridge"]
    outbound = modules["services.messenger.outbound"]

    entrypoints.register_user_entry(10001, platform="telegram", external_user_id="tg-10001")
    token = bridge.issue_bridge_token(10001, target_platform="vk")
    entrypoints.register_user_entry(
        20002,
        platform="vk",
        external_user_id="vk-20002",
        start_payload=f"bridge_{token}",
    )

    plan = outbound.build_delivery_plan(10001, preferred_platform="vk")

    assert plan.user_id == 10001
    assert plan.platform == "vk"
    assert plan.external_user_id == "vk-20002"


def test_bridge_token_rejects_wrong_target_platform(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    entrypoints = modules["services.messenger.entrypoints"]
    bridge = modules["services.messenger.bridge"]
    identity = modules["services.accounts.identity"]

    entrypoints.register_user_entry(10001, platform="telegram", external_user_id="tg-10001")
    token = bridge.issue_bridge_token(10001, target_platform="vk")

    result = entrypoints.register_user_entry(
        30003,
        platform="max",
        external_user_id="max-30003",
        start_payload=f"bridge_{token}",
    )

    assert result.user_id == 30003
    assert result.linked_via_bridge is False
    assert {row["platform"] for row in identity.get_account_snapshot(10001)["identities"]} == {"telegram"}
    assert {row["platform"] for row in identity.get_account_snapshot(30003)["identities"]} == {"max"}


def test_identity_conflict_blocks_unconfirmed_account_merge(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]

    identity.link_channel_to_account(10001, "vk", "vk-20002")

    with pytest.raises(identity.AccountIdentityConflict):
        identity.link_channel_to_account(30003, "vk", "vk-20002")

    assert {row["platform"] for row in identity.get_account_snapshot(10001)["identities"]} == {"vk"}
    assert identity.get_account_snapshot(30003)["identities"] == []


def test_account_audio_progress_is_channel_independent(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    audio = modules["services.accounts.audio_progress"]

    account_id = 10001
    assert audio.next_audio_no(account_id) == 1

    state = audio.mark_audio_sent(account_id, 1, platform="telegram", external_user_id="tg-10001")
    assert state.next_audio_no == 1

    state = audio.mark_audio_completed(account_id, 1, platform="telegram")
    assert state.next_audio_no == 2

    audio.mark_audio_completed(account_id, 2, platform="telegram")
    audio.mark_audio_completed(account_id, 3, platform="telegram")
    assert audio.next_audio_no(account_id) == 4

    state = audio.mark_audio_sent(account_id, 4, platform="vk", external_user_id="vk-20002")
    assert state.next_audio_no == 4

    state = audio.mark_audio_completed(account_id, 4, platform="vk")
    assert state.next_audio_no == 5
