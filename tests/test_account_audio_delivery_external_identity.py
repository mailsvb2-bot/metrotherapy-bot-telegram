from __future__ import annotations

import importlib


def _fresh_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("METRO_DB_PATH", str(tmp_path / "account_audio_delivery_external_identity.db"))
    monkeypatch.setenv("METRO_DB_ENGINE", "sqlite")

    modules = {}
    for name in [
        "core.paths",
        "services.db.core",
        "services.schema_core",
        "services.schema",
        "services.accounts.identity",
        "services.accounts.audio_progress",
    ]:
        module = importlib.import_module(name)
        modules[name] = importlib.reload(module)

    modules["services.schema"].init_db()
    return modules


def _last_delivery_row():
    db_core = importlib.import_module("services.db")
    with db_core.db() as conn:
        row = conn.execute(
            """
            SELECT account_id, platform, external_user_id, audio_no
            FROM account_audio_deliveries
            ORDER BY id DESC
            LIMIT 1
            """.strip()
        ).fetchone()
    return dict(row)


def test_mark_audio_sent_uses_linked_vk_external_id_instead_of_account_id(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    audio = modules["services.accounts.audio_progress"]

    identity.link_channel_to_account(10001, "telegram", "10001", verified=True)
    identity.link_channel_to_account(10001, "vk", "20002", verified=True)

    audio.mark_audio_sent(10001, 1, platform="vk", external_user_id="10001")

    row = _last_delivery_row()
    assert row["account_id"] == 10001
    assert row["platform"] == "vk"
    assert row["external_user_id"] == "20002"
    assert row["audio_no"] == 1


def test_mark_audio_sent_preserves_explicit_non_account_external_id(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    audio = modules["services.accounts.audio_progress"]

    identity.link_channel_to_account(10001, "telegram", "10001", verified=True)
    identity.link_channel_to_account(10001, "max", "30003", verified=True)

    audio.mark_audio_sent(10001, 2, platform="max", external_user_id="override-30003")

    row = _last_delivery_row()
    assert row["account_id"] == 10001
    assert row["platform"] == "max"
    assert row["external_user_id"] == "override-30003"
    assert row["audio_no"] == 2


def test_mark_audio_sent_uses_linked_external_id_when_missing(tmp_path, monkeypatch):
    modules = _fresh_modules(tmp_path, monkeypatch)
    identity = modules["services.accounts.identity"]
    audio = modules["services.accounts.audio_progress"]

    identity.link_channel_to_account(10001, "telegram", "10001", verified=True)

    audio.mark_audio_sent(10001, 3, platform="telegram")

    row = _last_delivery_row()
    assert row["account_id"] == 10001
    assert row["platform"] == "telegram"
    assert row["external_user_id"] == "10001"
    assert row["audio_no"] == 3
