from __future__ import annotations

import importlib


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
