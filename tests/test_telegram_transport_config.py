from config.settings import settings
from runtime import health_server
from runtime.telegram_transport import telegram_transport


def test_telegram_transport_defaults_to_polling(monkeypatch):
    monkeypatch.setattr(settings, 'TELEGRAM_TRANSPORT', 'polling')
    monkeypatch.setattr(settings, 'TELEGRAM_WEBHOOK_ENABLED', False)
    assert telegram_transport() == 'polling'


def test_telegram_transport_backcompat_flag_enables_webhook(monkeypatch):
    monkeypatch.setattr(settings, 'TELEGRAM_TRANSPORT', 'telegram')
    monkeypatch.setattr(settings, 'TELEGRAM_WEBHOOK_ENABLED', True)
    assert telegram_transport() == 'webhook'


def test_telegram_transport_polling_flag_enables_webhook(monkeypatch):
    monkeypatch.setattr(settings, 'TELEGRAM_TRANSPORT', 'polling')
    monkeypatch.setattr(settings, 'TELEGRAM_WEBHOOK_ENABLED', True)
    assert telegram_transport() == 'webhook'


def test_health_runtime_reports_any_webhook(monkeypatch):
    monkeypatch.setattr(health_server.settings, 'MESSENGER_WEBHOOK_ENABLED', False)
    monkeypatch.setattr(health_server.settings, 'TELEGRAM_TRANSPORT', 'webhook')
    monkeypatch.setattr(health_server.settings, 'TELEGRAM_WEBHOOK_ENABLED', False)
    assert health_server._webhook_configured() is True
