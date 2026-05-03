import importlib


def _reload_preflight(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'dev')
    import config.settings as settings_mod
    import interfaces.messaging.preflight as preflight
    importlib.reload(settings_mod)
    importlib.reload(preflight)
    return preflight


def test_max_preflight_reports_missing_webhook_secret(monkeypatch):
    preflight = _reload_preflight(monkeypatch)
    monkeypatch.setattr(preflight.settings, 'MAX_BOT_TOKEN', 'token')
    monkeypatch.setattr(preflight.settings, 'MAX_BOT_LINK_BASE', 'https://max.ru/bot')
    monkeypatch.setattr(preflight.settings, 'MAX_WEBHOOK_SECRET', '')
    monkeypatch.setattr(preflight.settings, 'MESSENGER_WEBHOOK_ENABLED', True)
    monkeypatch.setattr(preflight.settings, 'MESSENGER_PUBLIC_BASE_URL', 'https://metrotherapy.example')
    monkeypatch.setattr(preflight.settings, 'MAX_API_BASE_URL', 'https://platform-api.max.ru')

    status = preflight.check_max_preflight()

    assert status.channel == 'max'
    assert status.ok is False
    assert status.missing == ('MAX_WEBHOOK_SECRET',)
    assert status.details['webhook_url'] == 'https://metrotherapy.example/webhooks/max'


def test_max_preflight_rejects_legacy_domain(monkeypatch):
    preflight = _reload_preflight(monkeypatch)
    monkeypatch.setattr(preflight.settings, 'MAX_BOT_TOKEN', 'token')
    monkeypatch.setattr(preflight.settings, 'MAX_BOT_LINK_BASE', 'https://max.ru/bot')
    monkeypatch.setattr(preflight.settings, 'MAX_WEBHOOK_SECRET', 'secret')
    monkeypatch.setattr(preflight.settings, 'MESSENGER_WEBHOOK_ENABLED', True)
    monkeypatch.setattr(preflight.settings, 'MESSENGER_PUBLIC_BASE_URL', 'https://metrotherapy.example')
    monkeypatch.setattr(preflight.settings, 'MAX_API_BASE_URL', 'https://botapi.max.ru')

    status = preflight.check_max_preflight()

    assert status.ok is False
    assert any('botapi.max.ru' in warning for warning in status.warnings)


def test_vk_preflight_reports_required_fields(monkeypatch):
    preflight = _reload_preflight(monkeypatch)
    monkeypatch.setattr(preflight.settings, 'VK_GROUP_TOKEN', '')
    monkeypatch.setattr(preflight.settings, 'VK_CONFIRMATION_TOKEN', '')
    monkeypatch.setattr(preflight.settings, 'VK_GROUP_ID', '')
    monkeypatch.setattr(preflight.settings, 'MESSENGER_WEBHOOK_ENABLED', True)
    monkeypatch.setattr(preflight.settings, 'MESSENGER_PUBLIC_BASE_URL', '')

    status = preflight.check_vk_preflight()

    assert status.ok is False
    assert status.missing == ('VK_GROUP_TOKEN', 'VK_CONFIRMATION_TOKEN', 'VK_GROUP_ID', 'MESSENGER_PUBLIC_BASE_URL')


def test_all_preflights_returns_three_channels(monkeypatch):
    preflight = _reload_preflight(monkeypatch)

    statuses = preflight.check_all_preflights()

    assert [status.channel for status in statuses] == ['telegram', 'max', 'vk']
