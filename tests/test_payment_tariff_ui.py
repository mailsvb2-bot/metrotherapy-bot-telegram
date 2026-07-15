from __future__ import annotations

from services.payments.ui import kb_tariffs, kb_telegram_payment_methods, telegram_payment_method_text


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_public_telegram_tariff_keyboard_is_stars_only(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    buttons = _buttons(kb_tariffs(user_id=404))
    texts = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert "📦 Стартовый пакет: 1 226 звёзд" in texts
    assert "📦 Полный маршрут: 5 099 звёзд" in texts
    assert "📦 Антистресс-система: 8 327 звёзд" in texts
    assert "📦 Персональный месяц: 14 847 звёзд" in texts
    assert not any(button.url for button in buttons)
    assert not any("карт" in text.casefold() or "юkassa" in text.casefold() for text in texts)
    assert "pay:methods:practice_start_7" in callbacks


def test_payment_method_choice_contains_only_native_stars(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    buttons = _buttons(kb_telegram_payment_methods(user_id=404, package_id="practice_start_7"))
    callbacks = [str(button.callback_data) for button in buttons if button.callback_data]
    texts = [button.text for button in buttons]

    assert "stars:terms:practice_start_7" in callbacks
    assert not any(button.url for button in buttons)
    assert "⭐ Звёздами Telegram — 1 226 звёзд" in texts
    text = telegram_payment_method_text("practice_start_7")
    assert "проводится звёздами Telegram" in text
    assert "1 226 звёзд" in text
    assert "ЮKassa" not in text


def test_stars_emergency_switch_does_not_fall_back_to_external_checkout(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "0")
    buttons = _buttons(kb_telegram_payment_methods(user_id=405, package_id="practice_start_7"))
    texts = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert not any(button.url for button in buttons)
    assert "⭐ Оплата звёздами временно недоступна" in texts
    assert "tariffs:stars_disabled" in callbacks
    assert "временно недоступна" in telegram_payment_method_text("practice_start_7")


def test_legacy_telegram_yookassa_env_cannot_reenable_external_checkout(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    buttons = _buttons(kb_telegram_payment_methods(user_id=406, package_id="practice_start_7"))
    assert not any(button.url for button in buttons)
    assert not any("ЮKassa" in button.text for button in buttons)
