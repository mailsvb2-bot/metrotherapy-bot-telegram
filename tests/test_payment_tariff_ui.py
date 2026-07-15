from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from services.payments.checkout_intent import verify_checkout_intent
from services.payments.ui import (
    kb_tariffs,
    kb_telegram_payment_methods,
    telegram_payment_method_text,
)


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_public_telegram_tariff_keyboard_opens_payment_method_choice(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")

    markup = kb_tariffs(user_id=404)
    buttons = _buttons(markup)
    texts = [button.text for button in buttons]
    urls = [button.url for button in buttons if button.url]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert "📦 Стартовый пакет: картой 1 900 ₽ или 1 226 звёзд" in texts
    assert "📦 Полный маршрут: картой 7 900 ₽ или 5 099 звёзд" in texts
    assert "📦 Антистресс-система: картой 12 900 ₽ или 8 327 звёзд" in texts
    assert "📦 Персональный месяц: картой 23 000 ₽ или 14 847 звёзд" in texts
    assert not any(" / " in text or "XTR" in text or "RUB" in text for text in texts)
    assert not urls

    joined = "\n".join(texts + callbacks)
    assert "morning_5" not in joined
    assert "morning_20" not in joined
    assert "evening_5" not in joined
    assert "evening_20" not in joined
    assert "both_5" not in joined
    assert "both_20" not in joined

    assert "pay:methods:practice_start_7" in callbacks
    assert "pay:methods:practice_60" in callbacks
    assert "pay:methods:practice_antistress_60" in callbacks
    assert "pay:methods:practice_personal_month" in callbacks
    assert "stars:terms" in callbacks


def test_payment_method_choice_contains_stars_and_signed_yookassa(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "unit-test-checkout-key")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")

    buttons = _buttons(
        kb_telegram_payment_methods(
            user_id=404,
            package_id="practice_start_7",
        )
    )
    callbacks = [str(button.callback_data) for button in buttons if button.callback_data]
    urls = [str(button.url) for button in buttons if button.url]

    assert "stars:terms:practice_start_7" in callbacks
    assert len(urls) == 1
    params = parse_qs(urlsplit(urls[0]).query)
    assert params["source"] == ["telegram"]
    assert params["user_id"] == ["404"]
    assert params["package_id"] == ["practice_start_7"]
    verify_checkout_intent(
        params["intent"][0],
        expected_user_id=404,
        expected_package_id="practice_start_7",
    )

    texts = [button.text for button in buttons]
    assert "⭐ Звёздами Telegram — 1 226 звёзд" in texts
    assert "💳 Картой через ЮKassa — 1 900 ₽" in texts

    text = telegram_payment_method_text("practice_start_7")
    assert "Это две готовые цены одного пакета — пересчитывать ничего не нужно" in text
    assert "Звёздами Telegram — 1 226 звёзд" in text
    assert "Картой через ЮKassa — 1 900 ₽" in text
    assert "PROVIDER_ACCOUNT_INVALID" not in text
    assert "XTR" not in text
    assert "RUB" not in text


def test_stars_emergency_switch_keeps_yookassa_available(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")
    monkeypatch.setenv("PAYMENT_CHECKOUT_SIGNING_KEY", "unit-test-checkout-key")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "0")

    buttons = _buttons(
        kb_telegram_payment_methods(
            user_id=405,
            package_id="practice_start_7",
        )
    )
    texts = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert not any((callback or "").startswith("stars:terms:") for callback in callbacks)
    assert any(button.url and "/pay/yookassa?" in str(button.url) for button in buttons)
    assert "⭐ Оплата звёздами временно недоступна" in texts
    assert "tariffs:stars_disabled" in callbacks
    assert any("ЮKassa" in text for text in texts)


def test_telegram_yookassa_switch_removes_external_url(monkeypatch):
    monkeypatch.setenv("MESSENGER_PUBLIC_BASE_URL", "https://bot.example")
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "0")

    buttons = _buttons(
        kb_telegram_payment_methods(
            user_id=406,
            package_id="practice_start_7",
        )
    )
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert not any(button.url for button in buttons)
    assert "stars:terms:practice_start_7" in callbacks
    assert "tariffs:yookassa_disabled" in callbacks
    assert "Оплата картой через ЮKassa временно недоступна" in telegram_payment_method_text("practice_start_7")
