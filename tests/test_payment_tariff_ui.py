from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from services.payments.ui import kb_tariffs, kb_telegram_payment_methods, telegram_payment_method_text


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def _yookassa_button(buttons):
    return next(button for button in buttons if button.url and "ЮKassa" in button.text)


def test_public_telegram_tariff_keyboard_shows_both_prices(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    buttons = _buttons(kb_tariffs(user_id=404))
    texts = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert "📦 Стартовый пакет: 1 226 звёзд или 1 900 ₽" in texts
    assert "📦 Полный маршрут: 5 099 звёзд или 7 900 ₽" in texts
    assert "📦 Антистресс-система: 8 327 звёзд или 12 900 ₽" in texts
    assert "📦 Персональный месяц: 14 847 звёзд или 23 000 ₽" in texts
    assert not any(button.url for button in buttons)
    assert "pay:methods:practice_start_7" in callbacks


def test_payment_method_choice_contains_stars_and_signed_yookassa(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://metrotherapy.example")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "unit-test-checkout-signing-key")

    buttons = _buttons(kb_telegram_payment_methods(user_id=404, package_id="practice_start_7"))
    callbacks = [str(button.callback_data) for button in buttons if button.callback_data]
    texts = [button.text for button in buttons]

    assert "stars:terms:practice_start_7" in callbacks
    assert "⭐ Звёздами Telegram — 1 226 звёзд" in texts
    assert "💳 Картой через ЮKassa — 1 900 ₽" in texts

    yookassa = _yookassa_button(buttons)
    parsed = urlsplit(yookassa.url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert query["source"] == ["telegram"]
    assert query["user_id"] == ["404"]
    assert query["package_id"] == ["practice_start_7"]
    assert query["amount_minor"] == ["190000"]
    assert query["currency"] == ["RUB"]
    assert query["intent"][0]

    text = telegram_payment_method_text("practice_start_7")
    assert "Выберите способ оплаты" in text
    assert "1 226 звёзд" in text
    assert "1 900 ₽" in text
    assert "ЮKassa" in text


def test_stars_emergency_switch_falls_back_to_yookassa(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "0")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://metrotherapy.example")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "unit-test-checkout-signing-key")

    buttons = _buttons(kb_telegram_payment_methods(user_id=405, package_id="practice_start_7"))
    texts = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert "⭐ Оплата звёздами временно недоступна" in texts
    assert "tariffs:stars_disabled" in callbacks
    assert _yookassa_button(buttons).url
    assert "временно недоступна" in telegram_payment_method_text("practice_start_7")
    assert "ЮKassa" in telegram_payment_method_text("practice_start_7")


def test_telegram_yookassa_kill_switch_hides_external_checkout(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "0")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://metrotherapy.example")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "unit-test-checkout-signing-key")

    buttons = _buttons(kb_telegram_payment_methods(user_id=406, package_id="practice_start_7"))
    callbacks = [button.callback_data for button in buttons if button.callback_data]
    texts = [button.text for button in buttons]

    assert not any(button.url for button in buttons)
    assert "💳 Оплата через ЮKassa временно недоступна" in texts
    assert "tariffs:yookassa_disabled" in callbacks
