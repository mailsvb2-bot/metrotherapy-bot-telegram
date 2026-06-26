from __future__ import annotations

from runtime import messenger_max_ui as max_ui
from services.messenger.menu_contract import MAIN_MENU_ACTIONS
from services.messenger.text_ui import handle_incoming_text


def _button_texts(attachment: dict) -> list[str]:
    return [
        button["text"]
        for row in attachment["payload"]["buttons"]
        for button in row
    ]


def _button_commands(attachment: dict) -> list[str]:
    out: list[str] = []
    for row in attachment["payload"]["buttons"]:
        for button in row:
            out.append(str((button.get("payload") or {}).get("command") or ""))
    return out


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

    attachments = max_ui.native_keyboard_attachments(text)

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

    assert "отправьте:" not in max_ui.prepare_text(text, has_native_keyboard=True)
    assert "отправьте:" in max_ui.prepare_text(text, has_native_keyboard=False)


def test_max_demo_full_weather_and_score_surfaces_have_native_buttons() -> None:
    demo = max_ui.native_keyboard_attachments("🌿 Бесплатная практика\n\nВыберите короткий маршрут")
    full = max_ui.native_keyboard_attachments("🔐 Полный маршрут\n\nНажмите «🎧 Получить аудио»")
    weather = max_ui.native_keyboard_attachments("🌤 Погода\n\nВ MAX и ВКонтакте доступны те же базовые действия")
    score = max_ui.native_keyboard_attachments("🌿 Перед аудио оцените состояние сейчас от -10 до +10")

    assert _button_texts(demo[0]) == ["🚗 Практика на утро / дорогу", "🌙 Практика на вечер / домой", "⬅️ Меню"]
    assert _button_texts(full[0]) == ["🎧 Получить аудио", "✅ Прослушал", "⬅️ Меню"]
    assert _button_texts(weather[0]) == ["🔄 Обновить погоду", "🏙 Изменить город", "⬅️ Меню"]
    assert "-10" in _button_texts(score[0])
    assert "10" in _button_texts(score[0])
    assert "📈 Мой прогресс" in _button_texts(score[0])


def test_max_progress_text_does_not_trigger_score_scale_keyboard() -> None:
    progress_text = (
        "🎧 Общий прогресс аудио\n\n"
        "📈 Мой прогресс и анализ состояния\n\n"
        "Чтобы добавить новую оценку состояния, отправьте число от -10 до 10 после прослушивания аудио."
    )

    attachments = max_ui.native_keyboard_attachments(progress_text)

    assert len(attachments) == 1
    assert _button_commands(attachments[0]) == ["continue", "done", "repeat", "history", "start"]
    assert "-10" not in _button_texts(attachments[0])
    assert "10" not in _button_texts(attachments[0])


def test_max_payment_and_gift_surfaces_use_link_buttons() -> None:
    payment = max_ui.native_keyboard_attachments(
        "💳 Оплата доступа к Метротерапии\n\nhttps://metrotherapy-bot.metrotherapy.ru/pay/yookassa?source=max"
    )
    gift = max_ui.native_keyboard_attachments(
        "🎁 Подарить Метротерапию\n\nhttps://metrotherapy-bot.metrotherapy.ru/pay/yookassa?kind=gift"
    )

    assert _button_types(payment[0]) == {"link", "message"}
    assert payment[0]["payload"]["buttons"][0][0]["text"] == "💳 Оплатить"
    assert payment[0]["payload"]["buttons"][0][0]["type"] == "link"
    assert gift[0]["payload"]["buttons"][0][0]["text"] == "🎁 Оплатить подарок"
    assert gift[0]["payload"]["buttons"][0][0]["type"] == "link"


def test_max_text_surfaces_are_not_vk_only() -> None:
    for text in [
        handle_incoming_text(951001, platform="max", external_user_id="951001", text="demo")[1][0].text,
        handle_incoming_text(951002, platform="max", external_user_id="951002", text="full")[1][0].text,
        handle_incoming_text(951003, platform="max", external_user_id="951003", text="weather")[1][0].text,
        "✅ Принял: аудио уже было отмечено как доставленное во ВКонтакте.\n\nТеперь оцените состояние ПОСЛЕ прослушивания.",
    ]:
        prepared = max_ui.prepare_text(text, has_native_keyboard=True)
        assert "внутри ВКонтакте" not in prepared
        assert "во ВКонтакте" not in prepared
        assert "в этот мессенджер" in prepared or "в этом мессенджере" in prepared or "MAX и ВКонтакте" in prepared
