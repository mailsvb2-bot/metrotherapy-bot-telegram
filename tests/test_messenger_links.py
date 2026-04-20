from services.messenger.links import build_messenger_targets
from config.settings import settings


def test_build_targets_uses_available_messengers(monkeypatch):
    monkeypatch.setattr(settings, 'TELEGRAM_BOT_USERNAME', 'metro_test_bot')
    monkeypatch.setattr(settings, 'MAX_BOT_LINK_BASE', 'https://max.ru/metrotherapy')
    monkeypatch.setattr(settings, 'MAX_BOT_NAME', 'metrotherapy_bot')
    monkeypatch.setattr(settings, 'VK_GROUP_ID', '123456')

    items = build_messenger_targets(42)
    platforms = [item['platform'] for item in items]

    assert platforms == ['telegram', 'max', 'vk']
    assert items[0]['url'].endswith('start=ref_42')
    assert 'start=ref_42' in items[1]['url']
    assert 'start=ref_42' in items[2]['url']
