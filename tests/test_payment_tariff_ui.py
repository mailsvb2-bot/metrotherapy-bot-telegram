from __future__ import annotations

from services.payments.ui import kb_tariffs


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_public_telegram_tariff_keyboard_is_stars_only(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")

    markup = kb_tariffs(user_id=404)
    buttons = _buttons(markup)
    texts = [button.text for button in buttons]
    urls = [button.url for button in buttons if button.url]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert "⭐ Стартовый пакет — 1 226 ⭐" in texts
    assert "⭐ Полный маршрут — 5 099 ⭐" in texts
    assert "⭐ Антистресс-система — 8 327 ⭐" in texts
    assert "⭐ Персональный месяц — 14 847 ⭐" in texts
    assert not any("YooKassa" in text for text in texts)
    assert not urls

    joined = "\n".join(texts + callbacks)
    assert "morning_5" not in joined
    assert "morning_20" not in joined
    assert "evening_5" not in joined
    assert "evening_20" not in joined
    assert "both_5" not in joined
    assert "both_20" not in joined

    assert "stars:terms:practice_start_7" in callbacks
    assert "stars:terms:practice_60" in callbacks
    assert "stars:terms:practice_antistress_60" in callbacks
    assert "stars:terms:practice_personal_month" in callbacks
    assert "stars:terms" in callbacks


def test_stars_emergency_switch_fails_closed_inside_telegram(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "0")

    buttons = _buttons(kb_tariffs(user_id=405))
    texts = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert not any((callback or "").startswith("stars:terms:") for callback in callbacks)
    assert not any(button.url for button in buttons)
    assert "⭐ Оплата временно недоступна" in texts
    assert "tariffs:stars_disabled" in callbacks
    assert not any("YooKassa" in text for text in texts)
