import importlib


def _reload_modules(monkeypatch, tmp_path):
    # These are unit/integration tests over SQLite-backed messenger helpers,
    # not production boot tests. Keep them isolated from server-level APP_ENV
    # and optional messenger secrets that may be present in systemd/.env.
    monkeypatch.setenv('APP_ENV', 'dev')
    monkeypatch.delenv('MAX_WEBHOOK_SECRET', raising=False)
    monkeypatch.setenv('METRO_DB_PATH', str(tmp_path / 'test.db'))
    core_paths = importlib.import_module('core.paths')
    db_core = importlib.import_module('services.db.core')
    schema_core = importlib.import_module('services.schema_core')
    schema = importlib.import_module('services.schema')
    dedupe = importlib.import_module('services.messenger.webhook_dedupe')
    prefs = importlib.import_module('services.messenger.preferences')
    entry = importlib.import_module('services.messenger.entrypoints')
    bridge = importlib.import_module('services.messenger.bridge')
    settings_mod = importlib.import_module('config.settings')
    importlib.reload(core_paths)
    importlib.reload(db_core)
    importlib.reload(schema_core)
    importlib.reload(settings_mod)
    importlib.reload(schema)
    importlib.reload(dedupe)
    importlib.reload(prefs)
    importlib.reload(bridge)
    importlib.reload(entry)
    schema.init_db()
    return dedupe, prefs, bridge, entry, settings_mod.settings


def test_webhook_dedupe_rejects_duplicate(monkeypatch, tmp_path):
    dedupe, *_ = _reload_modules(monkeypatch, tmp_path)
    event_key = f"vk:test:{tmp_path.name}"
    payload = {'type': 'message_new', 'object': {'message': {'id': event_key, 'from_id': 10}}}
    assert dedupe.register_inbound_event('vk', event_key, payload) is True
    assert dedupe.register_inbound_event('vk', event_key, payload) is False


def test_bridge_entry_makes_current_platform_preferred(monkeypatch, tmp_path):
    _, prefs, bridge, entry, settings = _reload_modules(monkeypatch, tmp_path)
    token = bridge.issue_bridge_token(501)
    result = entry.register_user_entry(9901, platform='vk', external_user_id='vk-9901', start_payload=f'bridge_{token}')
    assert result.linked_via_bridge is True
    assert prefs.get_preferred_platform(501) == 'vk'


def test_identity_conflict_collapses_to_latest_canonical_user(monkeypatch, tmp_path):
    _, prefs, *_ = _reload_modules(monkeypatch, tmp_path)
    prefs.record_channel_identity(701, 'vk', 'same-ext')
    prefs.record_channel_identity(702, 'vk', 'same-ext')
    snap_701 = prefs.get_channel_snapshot(701)
    snap_702 = prefs.get_channel_snapshot(702)
    assert snap_701['identities'] == []
    assert snap_702['identities'][0]['external_user_id'] == 'same-ext'



import pytest


@pytest.mark.asyncio
async def test_send_reply_bundle_does_not_duplicate_audio_link_message(monkeypatch):
    from runtime.messenger_webhooks import _send_reply_bundle
    from services.messenger.text_ui import MessengerReply

    sent = []

    class FakeSender:
        async def send_text(self, external_user_id, text, **kwargs):
            sent.append((external_user_id, text))

    async def fake_send_next_audio_to_user(user_id, *, senders, telegram_bot=None, fallback='telegram', target_platform=None):
        await senders.get('vk').send_text('vk-1', 'LINK_MESSAGE')
        class Result:
            transport = 'messenger_link'
            message = 'SHOULD_NOT_BE_SENT_TWICE'
        return Result()

    monkeypatch.setattr('runtime.messenger_webhooks.VkBotSender', lambda: FakeSender())
    monkeypatch.setattr('runtime.messenger_webhooks.MaxBotSender', lambda: FakeSender())
    monkeypatch.setattr('runtime.messenger_webhooks.send_next_audio_to_user', fake_send_next_audio_to_user)

    await _send_reply_bundle('vk', 'vk-1', 1, [MessengerReply(kind='next_audio')])
    assert sent == [('vk-1', 'LINK_MESSAGE')]
