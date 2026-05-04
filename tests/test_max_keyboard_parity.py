from __future__ import annotations

from runtime.messenger_senders import MaxBotSender
from services.messenger.menu_contract import MAIN_MENU_ACTIONS


def _button_texts(attachment: dict) -> list[str]:
    return [
        button["text"]
        for row in attachment["payload"]["buttons"]
        for button in row
    ]


def _button_types(attachment: dict) -> set[str]:
    return {
        button["type"]
        for row in attachment["payload"]["buttons"]
        for button in row
    }


def test_max_main_menu_gets_native_buttons_equal_to_canonical_menu() -> None:
    text = (
        "Главное меню\n\n"
        "Кнопки MAX и ВКонтакте соответствуют главному меню Telegram:\n"
        "• 🌿 Попробовать бесплатно\n"
        "• 🔐 Полный маршрут\n"
        "• 💳 Тарифы\n"
        "• 🎁 Подарить\n"
        "• 📈 Мой прогресс\n"
        "• 🧠 Настройки\n"
        "• 📣 Посоветовать\n"
        "• 🌤 Погода"
    )

    attachments = MaxBotSender._native_keyboard_attachments(text)

    assert len(attachments) == 1
    assert attachments[0]["type"] == "inline_keyboard"
    assert _button_texts(attachments[0]) == [action.title for action in MAIN_MENU_ACTIONS]
    assert _button_types(attachments[0]) == {"message"}


def test_max_main_menu_text_fallback_is_only_added_without_native_keyboard() -> None:
    text = (
        "Главное меню\n\n"
        "Кнопки MAX и ВКонтакте соответствуют главному меню Telegram:\n"
        "• 🌿 Попробовать бесплатно\n"
    )

    assert "отправьте:" not in MaxBotSender._prepare_text(text, has_native_keyboard=True)
    assert "отправьте:" in MaxBotSender._prepare_text(text, has_native_keyboard=False)


def test_max_demo_full_weather_and_score_surfaces_have_native_buttons() -> None:
    demo = MaxBotSender._native_keyboard_attachments("🌿 Бесплатная практика\n\nВыберите короткий маршрут")
    full = MaxBotSender._native_keyboard_attachments("🔐 Полный маршрут\n\nНажмите «🎧 Получить аудио»")
    weather = MaxBotSender._native_keyboard_attachments("🌤 Погода\n\nВо ВКонтакте доступны те же базовые действия")
    score = MaxBotSender._native_keyboard_attachments("🌿 Перед аудио оцените состояние сейчас от -10 до +10")

    assert _button_texts(demo[0]) == ["1️⃣ Утро / дорога", "2️⃣ Вечер / домой", "⬅️ Меню"]
    assert _button_texts(full[0]) == ["🎧 Получить аудио", "✅ Прослушал", "⬅️ Меню"]
    assert _button_texts(weather[0]) == ["🔄 Обновить погоду", "🏙 Изменить город", "⬅️ Меню"]
    assert "-10" in _button_texts(score[0])
    assert "10" in _button_texts(score[0])
    assert "📈 Мой прогресс" in _button_texts(score[0])


def test_max_payment_and_gift_surfaces_use_link_buttons() -> None:
    payment = MaxBotSender._native_keyboard_attachments(
        "💳 Оплата доступа к Метротерапии\n\nhttps://metrotherapy-bot.metrotherapy.ru/pay/yookassa?source=max"
    )
    gift = MaxBotSender._native_keyboard_attachments(
        "🎁 Подарить Метротерапию\n\nhttps://metrotherapy-bot.metrotherapy.ru/pay/yookassa?kind=gift"
    )

    assert _button_types(payment[0]) == {"link", "message"}
    assert payment[0]["payload"]["buttons"][0][0]["text"] == "💳 Оплатить"
    assert payment[0]["payload"]["buttons"][0][0]["type"] == "link"
    assert gift[0]["payload"]["buttons"][0][0]["text"] == "🎁 Оплатить подарок"
    assert gift[0]["payload"]["buttons"][0][0]["type"] == "link"
