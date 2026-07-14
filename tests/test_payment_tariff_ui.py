from __future__ import annotations

from services.payments.ui import kb_tariffs


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_public_tariff_keyboard_uses_stars_and_yookassa(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")

    markup = kb_tariffs(user_id=404)
    buttons = _buttons(markup)
    texts = [button.text for button in buttons]
    urls = [button.url for button in buttons if button.url]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert "⭐ Stars · Стартовый пакет — 1 900 ⭐" in texts
    assert "⭐ Stars · Полный маршрут — 7 900 ⭐" in texts
    assert "⭐ Stars · Антистресс-система — 12 900 ⭐" in texts
    assert "⭐ Stars · Персональный месяц — 23 000 ⭐" in texts

    assert "💳 YooKassa · Стартовый пакет — 1 900 ₽" in texts
    assert "💳 YooKassa · Полный маршрут — 7 900 ₽" in texts
    assert "💳 YooKassa · Антистресс-система — 12 900 ₽" in texts
    assert "💳 YooKassa · Персональный месяц — 23 000 ₽" in texts

    joined = "\n".join(texts + urls + callbacks)
    assert "morning_5" not in joined
    assert "morning_20" not in joined
    assert "evening_5" not in joined
    assert "evening_20" not in joined
    assert "both_5" not in joined
    assert "both_20" not in joined

    assert "stars:buy:practice_start_7" in callbacks
    assert "stars:buy:practice_60" in callbacks
    assert "stars:buy:practice_antistress_60" in callbacks
    assert "stars:buy:practice_personal_month" in callbacks

    assert any("kind=tokens" in url and "package_id=practice_start_7" in url for url in urls)
    assert any("kind=tokens" in url and "package_id=practice_60" in url for url in urls)
    assert any("kind=tokens" in url and "package_id=practice_antistress_60" in url for url in urls)
    assert any("kind=tokens" in url and "package_id=practice_personal_month" in url for url in urls)


def test_stars_emergency_switch_does_not_remove_yookassa(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "0")

    buttons = _buttons(kb_tariffs(user_id=405))
    assert not any((button.callback_data or "").startswith("stars:") for button in buttons)
    yookassa = [button for button in buttons if button.url]
    assert len(yookassa) == 4
    assert all("/pay/yookassa?" in str(button.url) for button in yookassa)
