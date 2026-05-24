from __future__ import annotations

from services.messenger import preflight


def test_max_preflight_reports_missing_required_fields(monkeypatch):
    monkeypatch.setattr(preflight.settings, 'MAX_BOT_TOKEN', '')
    monkeypatch.setattr(preflight.settings, 'MAX_BOT_LINK_BASE', '')
    monkeypatch.setattr(preflight.settings, 'MESSENGER_WEBHOOK_ENABLED', True)
    monkeypatch.setattr(preflight.settings, 'MESSENGER_PUBLIC_BASE_URL', '')

    status = preflight.check_max_preflight()

    assert status.channel == 'max'
    assert status.ok is False
    assert status.missing == ('MAX_BOT_TOKEN', 'MAX_BOT_LINK_BASE', 'MESSENGER_PUBLIC_BASE_URL')
    assert status.details == {'webhook_url': ''}


def test_max_preflight_rejects_legacy_domain(monkeypatch):
    monkeypatch.setattr(preflight.settings, 'MAX_BOT_TOKEN', 'token')
    monkeypatch.setattr(preflight.settings, 'MAX_BOT_LINK_BASE', 'https://max.ru/bot')
    monkeypatch.setattr(preflight.settings, 'MESSENGER_WEBHOOK_ENABLED', True)
    monkeypatch.setattr(preflight.settings, 'MESSENGER_PUBLIC_BASE_URL', 'https://metrotherapy.example')
    monkeypatch.setattr(preflight.settings, 'MAX_API_BASE_URL', 'https://botapi.max.ru')

    status = preflight.check_max_preflight()

    assert status.ok is True
    assert any('botapi.max.ru' in warning for warning in status.warnings)
    assert status.details == {'webhook_url': 'https://metrotherapy.example/webhooks/max'}


def test_vk_preflight_reports_required_fields_and_secret_warning(monkeypatch):
    monkeypatch.setattr(preflight.settings, 'VK_GROUP_TOKEN', '')
    monkeypatch.setattr(preflight.settings, 'VK_CONFIRMATION_TOKEN', '')
    monkeypatch.setattr(preflight.settings, 'VK_GROUP_ID', '')
    monkeypatch.setattr(preflight.settings, 'VK_SECRET', '')
    monkeypatch.setattr(preflight.settings, 'MESSENGER_WEBHOOK_ENABLED', True)
    monkeypatch.setattr(preflight.settings, 'MESSENGER_PUBLIC_BASE_URL', '')

    status = preflight.check_vk_preflight()

    assert status.channel == 'vk'
    assert status.ok is False
    assert status.missing == ('VK_GROUP_TOKEN', 'VK_CONFIRMATION_TOKEN', 'VK_GROUP_ID', 'MESSENGER_PUBLIC_BASE_URL')
    assert 'VK_SECRET is not configured' in status.warnings[0]


def test_telegram_preflight_reports_webhook_requirements(monkeypatch):
    monkeypatch.setattr(preflight.settings, 'BOT_TOKEN', '')
    monkeypatch.setattr(preflight.settings, 'TELEGRAM_TRANSPORT', 'webhook')
    monkeypatch.setattr(preflight.settings, 'TELEGRAM_WEBHOOK_ENABLED', True)
    monkeypatch.setattr(preflight.settings, 'TELEGRAM_WEBHOOK_PUBLIC_BASE_URL', 'http://metrotherapy.example')
    monkeypatch.setattr(preflight.settings, 'TELEGRAM_WEBHOOK_SECRET_TOKEN', '')

    status = preflight.check_telegram_preflight()

    assert status.channel == 'telegram'
    assert status.ok is False
    assert status.missing == ('BOT_TOKEN', 'TELEGRAM_WEBHOOK_SECRET_TOKEN')
    assert 'https://' in status.warnings[0]
    assert status.details == {'transport': 'webhook', 'webhook_enabled': True}


def test_all_preflights_returns_three_channels():
    statuses = preflight.check_all_preflights()

    assert [status.channel for status in statuses] == ['telegram', 'max', 'vk']
