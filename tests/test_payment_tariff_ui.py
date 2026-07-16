from __future__ import annotations

from services.payments.ui import kb_tariffs, kb_telegram_payment_methods, telegram_payment_method_text


TOPUP_URL = "tg://stars_topup?balance=1500&purpose=metrotherapy_practice_start_7"


def _buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


def test_public_telegram_tariff_keyboard_shows_only_stars(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "explicit")
    buttons = _buttons(kb_tariffs(user_id=404))
    texts = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert "📦 Стартовый пакет — 1 500 Stars" in texts
    assert "📦 Полный маршрут — 2 500 Stars" in texts
    assert "📦 Антистресс-система — 5 000 Stars" in texts
    assert "📦 Персональный месяц — 15 000 Stars" in texts
    assert not any("₽" in text or "ЮKassa" in text for text in texts)
    assert not any(button.url for button in buttons)
    assert "pay:methods:practice_start_7" in callbacks


def test_stars_choice_separates_existing_balance_from_topup(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "explicit")

    buttons = _buttons(kb_telegram_payment_methods(user_id=404, package_id="practice_start_7"))
    callbacks = [str(button.callback_data) for button in buttons if button.callback_data]
    texts = [button.text for button in buttons]

    assert texts[0] == "⭐ У меня уже есть Stars — оплатить Метротерапию"
    assert buttons[0].callback_data == "stars:terms:practice_start_7"
    assert texts[1] == "➕ Сначала купить 1 500 Stars"
    assert buttons[1].url == TOPUP_URL
    assert texts[2] == "✅ Stars куплены — оплатить Метротерапию"
    assert buttons[2].callback_data == "stars:terms:practice_start_7"
    assert not any("ЮKassa" in text or "₽" in text for text in texts)
    assert not any((button.url or "").startswith("https://") for button in buttons)
    assert callbacks.count("stars:terms:practice_start_7") == 2

    text = telegram_payment_method_text("practice_start_7")
    assert "Выберите, что подходит Вам" in text
    assert "Stars уже есть" in text
    assert "Stars пока нет" in text
    assert "1 500 Stars" in text
    assert "ничего не списывается" in text
    assert "ЮKassa" not in text
    assert "₽" not in text


def test_stars_disabled_does_not_fall_back_to_yookassa(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "0")
    monkeypatch.setenv("TELEGRAM_YOOKASSA_ENABLED", "1")

    buttons = _buttons(kb_telegram_payment_methods(user_id=405, package_id="practice_start_7"))
    texts = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons if button.callback_data]

    assert "⭐ Оплата Stars временно недоступна" in texts
    assert "tariffs:stars_disabled" in callbacks
    assert not any("ЮKassa" in text or "₽" in text for text in texts)
    assert not any(button.url for button in buttons)
    assert "временно недоступна" in telegram_payment_method_text("practice_start_7")


def test_gift_stars_choice_keeps_gift_context(monkeypatch):
    monkeypatch.setenv("TELEGRAM_STARS_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_STARS_PRICING_MODE", "explicit")

    buttons = _buttons(kb_telegram_payment_methods(user_id=406, package_id="practice_start_7", gift=True))

    assert buttons[0].text == "⭐ У меня уже есть Stars — оплатить подарок"
    assert buttons[0].callback_data == "stars:gift_terms:practice_start_7"
    assert buttons[1].url == TOPUP_URL
    assert buttons[2].text == "✅ Stars куплены — оплатить подарок"
    assert buttons[2].callback_data == "stars:gift_terms:practice_start_7"
