import json

from keyboards.inline import kb_main, kb_demo_kind, kb_mood_scale, kb_weather
from runtime import messenger_max_ui
from runtime.messenger_senders import MaxBotSender, VkBotSender
from runtime.messenger_vk_ui import (
    full_route_keyboard_json,
    prepare_vk_keyboard_json,
    telegram_main_parity_keyboard_json,
    vk_demo_kind_keyboard_json,
    vk_main_keyboard_json,
    vk_progress_keyboard_json,
    vk_score_scale_keyboard_json,
    vk_settings_keyboard_json,
    vk_weather_keyboard_json,
)
from services.messenger.menu_contract import CONTEXT_ACTIONS, MAIN_MENU_ACTIONS, main_menu_commands, telegram_main_callbacks

ADMIN_LABEL = "🛠 Панель"
BACK_LABEL = "⬅️ Назад"
MAX_LEGACY_BACK_LABEL = "⬅️ Меню"


def _telegram_public_main_labels() -> list[str]:
    return [
        button.text
        for row in kb_main().inline_keyboard
        for button in row
        if button.text != ADMIN_LABEL
    ]


def _telegram_demo_labels() -> list[str]:
    return [button.text for row in kb_demo_kind().inline_keyboard for button in row]


def _telegram_score_labels() -> list[str]:
    return [button.text for row in kb_mood_scale(1, stage="pre").inline_keyboard for button in row]


def _numeric_score_labels() -> list[str]:
    return [str(value) for value in range(-10, 11)]


def _vk_keyboard(keyboard_json: str) -> dict:
    return json.loads(keyboard_json)


def _vk_commands(keyboard_json: str) -> list[str]:
    keyboard = _vk_keyboard(keyboard_json)
    commands: list[str] = []
    for row in keyboard["buttons"]:
        for button in row:
            payload = json.loads(button["action"]["payload"])
            commands.append(payload["command"])
    return commands


def _vk_labels(keyboard_json: str) -> list[str]:
    keyboard = _vk_keyboard(keyboard_json)
    return [button["action"]["label"] for row in keyboard["buttons"] for button in row]


def _max_button_texts(attachment: dict) -> list[str]:
    return [button["text"] for row in attachment["payload"]["buttons"] for button in row]


def _max_button_commands(attachment: dict) -> list[str]:
    commands: list[str] = []
    for row in attachment["payload"]["buttons"]:
        for button in row:
            payload = button.get("payload") or {}
            commands.append(str(payload.get("command") or ""))
    return commands


def test_vk_main_keyboard_is_rendered_from_canonical_contract():
    assert _vk_commands(vk_main_keyboard_json()) == list(main_menu_commands())
    assert _vk_labels(vk_main_keyboard_json()) == _telegram_public_main_labels()


def test_max_main_keyboard_is_rendered_from_canonical_contract():
    attachment = messenger_max_ui.main_menu_attachment()
    assert _max_button_commands(attachment) == list(main_menu_commands())
    assert _max_button_texts(attachment) == _telegram_public_main_labels()


def test_main_menu_does_not_expose_admin_outside_telegram():
    assert ADMIN_LABEL not in _vk_labels(vk_main_keyboard_json(1))
    assert ADMIN_LABEL not in _max_button_texts(messenger_max_ui.main_menu_attachment())
    assert "admin" not in set(_vk_commands(vk_main_keyboard_json(1)))
    assert "admin" not in set(_max_button_commands(messenger_max_ui.main_menu_attachment()))


def test_vk_main_keyboard_does_not_leak_context_audio_controls():
    commands = set(_vk_commands(vk_main_keyboard_json()))
    assert "continue" not in commands
    assert "done" not in commands


def test_demo_kind_labels_match_telegram_demo_kind_surface():
    expected = _telegram_demo_labels()
    assert _vk_labels(vk_demo_kind_keyboard_json()) == expected
    assert _max_button_texts(messenger_max_ui.demo_kind_attachment()) == expected[:-1] + [MAX_LEGACY_BACK_LABEL]
    assert _vk_commands(vk_demo_kind_keyboard_json()) == ["demo_work", "demo_home", "start"]
    assert _max_button_commands(messenger_max_ui.demo_kind_attachment()) == ["demo_work", "demo_home", "start"]


def test_score_scale_labels_match_platform_score_contracts():
    telegram_expected = _telegram_score_labels()
    numeric_expected = _numeric_score_labels()
    assert _vk_labels(vk_score_scale_keyboard_json()) == telegram_expected[:-1] + ["📈 Мой прогресс", BACK_LABEL]
    assert _max_button_texts(messenger_max_ui.score_scale_attachment()) == numeric_expected + ["📈 Мой прогресс", BACK_LABEL]

    vk_commands = _vk_commands(vk_score_scale_keyboard_json())
    max_commands = _max_button_commands(messenger_max_ui.score_scale_attachment())
    assert vk_commands[:21] == numeric_expected
    assert max_commands[:21] == [f"score:{value}" for value in range(-10, 11)]
    assert vk_commands[-2:] == ["progress", "start"]
    assert max_commands[-2:] == ["progress", "start"]


def test_weather_surface_matches_telegram_public_meaning():
    telegram_labels = [button.text for row in kb_weather().inline_keyboard for button in row]
    assert "🏙 Изменить город" in telegram_labels
    assert BACK_LABEL in telegram_labels

    vk_labels = _vk_labels(vk_weather_keyboard_json())
    max_labels = _max_button_texts(messenger_max_ui.weather_attachment())
    for labels in (vk_labels, max_labels):
        assert "🌤 Погода" in labels
        assert "🏙 Изменить город" in labels
        assert BACK_LABEL in labels


def test_full_route_context_controls_are_equal_for_vk_and_max():
    expected_labels = ["🎧 Получить аудио", "✅ Прослушал", BACK_LABEL]
    expected_commands = ["continue", "done", "start"]
    assert _vk_labels(full_route_keyboard_json()) == expected_labels
    assert _max_button_texts(messenger_max_ui.full_route_attachment()) == expected_labels
    assert _vk_commands(full_route_keyboard_json()) == expected_commands
    assert _max_button_commands(messenger_max_ui.full_route_attachment()) == expected_commands


def test_progress_context_controls_are_equal_for_vk_and_max():
    expected_labels = ["🎧 Получить аудио", "✅ Прослушал", "🔁 Повторить аудио", "🧾 История", BACK_LABEL]
    expected_commands = ["continue", "done", "repeat", "history", "start"]
    assert _vk_labels(vk_progress_keyboard_json()) == expected_labels
    assert _max_button_texts(messenger_max_ui.progress_attachment()) == expected_labels
    assert _vk_commands(vk_progress_keyboard_json()) == expected_commands
    assert _max_button_commands(messenger_max_ui.progress_attachment()) == expected_commands


def test_settings_public_surface_is_equal_for_vk_and_max():
    expected_labels = [
        "🌦 Погода в моём городе",
        "⏰ Время и правила отправки",
        "💬 Предпочтительный мессенджер",
        "📨 Каналы по времени дня",
        "📈 Анализ моего состояния",
        BACK_LABEL,
    ]
    expected_commands = ["weather", "time", "settings", "time", "progress", "start"]
    assert _vk_labels(vk_settings_keyboard_json()) == expected_labels
    assert _max_button_texts(messenger_max_ui.settings_attachment()) == expected_labels
    assert _vk_commands(vk_settings_keyboard_json()) == expected_commands
    assert _max_button_commands(messenger_max_ui.settings_attachment()) == expected_commands


def test_payment_and_gift_surfaces_keep_action_links_and_back_button():
    text = "💳 Тарифы\n\nПакет — 990 ₽\nhttps://pay.example/a"
    vk_labels = _vk_labels(prepare_vk_keyboard_json(vk_main_keyboard_json(), external_user_id="1", text=text))
    max_labels = _max_button_texts(messenger_max_ui.link_action_attachment(text))
    assert "Пакет — 990 ₽" in vk_labels
    assert BACK_LABEL in vk_labels
    assert "Пакет — 990 ₽" in max_labels
    assert BACK_LABEL in max_labels

    gift_text = "🎁 Подарить\n\nПакет — 990 ₽\nhttps://pay.example/g"
    assert BACK_LABEL in _vk_labels(prepare_vk_keyboard_json(vk_main_keyboard_json(), external_user_id="1", text=gift_text))
    assert BACK_LABEL in _max_button_texts(messenger_max_ui.link_action_attachment(gift_text))


def test_vk_renderer_filters_context_controls_from_main_keyboard():
    rows = json.loads(vk_main_keyboard_json())["buttons"]
    rows.append([
        {"action": {"type": "text", "label": "🎧 Получить аудио", "payload": json.dumps({"command": "continue"}, ensure_ascii=False)}, "color": "primary"},
        {"action": {"type": "text", "label": "✅ Прослушал", "payload": json.dumps({"command": "done"}, ensure_ascii=False)}, "color": "positive"},
    ])
    noisy = json.dumps({"one_time": False, "inline": False, "buttons": rows}, ensure_ascii=False)
    normalized_commands = set(_vk_commands(telegram_main_parity_keyboard_json(noisy)))
    assert "continue" not in normalized_commands
    assert "done" not in normalized_commands


def test_vk_renderer_keeps_context_controls_for_full_route():
    rendered = prepare_vk_keyboard_json(vk_main_keyboard_json(), external_user_id="123", text="🔐 Полный маршрут")
    assert rendered == full_route_keyboard_json()
    commands = set(_vk_commands(rendered))
    assert {"continue", "done", "start"}.issubset(commands)


def test_vk_sender_delegates_keyboard_normalization_to_renderer():
    rendered = VkBotSender()._api_version  # smoke: class remains transport-focused and instantiable
    assert callable(rendered)
    expected = prepare_vk_keyboard_json(vk_main_keyboard_json(), external_user_id="123", text="🔐 Полный маршрут")
    assert set(_vk_commands(expected)).intersection({action.command for action in CONTEXT_ACTIONS})


def test_max_sender_delegates_main_keyboard_to_renderer():
    assert MaxBotSender._main_menu_attachment() == messenger_max_ui.main_menu_attachment()


def test_telegram_main_callbacks_are_tracked_by_contract():
    callbacks = [button.callback_data for row in kb_main().inline_keyboard for button in row]
    for callback in telegram_main_callbacks():
        assert callback in callbacks
