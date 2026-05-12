import json

from keyboards.inline import kb_main
from runtime import messenger_max_ui
from runtime.messenger_senders import MaxBotSender, VkBotSender
from runtime.messenger_vk_ui import (
    full_route_keyboard_json,
    prepare_vk_keyboard_json,
    telegram_main_parity_keyboard_json,
    vk_demo_kind_keyboard_json,
    vk_main_keyboard_json,
)
from services.messenger.menu_contract import CONTEXT_ACTIONS, MAIN_MENU_ACTIONS, main_menu_commands, telegram_main_callbacks


def _vk_commands(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    commands: list[str] = []
    for row in keyboard["buttons"]:
        for button in row:
            payload = json.loads(button["action"]["payload"])
            commands.append(payload["command"])
    return commands


def _vk_labels(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    return [button["action"]["label"] for row in keyboard["buttons"] for button in row]


def _max_button_texts(attachment: dict) -> list[str]:
    return [button["text"] for row in attachment["payload"]["buttons"] for button in row]


def test_vk_main_keyboard_is_rendered_from_canonical_contract():
    assert _vk_commands(vk_main_keyboard_json()) == list(main_menu_commands())
    assert _vk_labels(vk_main_keyboard_json()) == [action.title for action in MAIN_MENU_ACTIONS]


def test_vk_main_keyboard_does_not_leak_context_audio_controls():
    commands = set(_vk_commands(vk_main_keyboard_json()))
    assert "continue" not in commands
    assert "done" not in commands


def test_vk_admin_keyboard_matches_telegram_admin_surface(monkeypatch):
    monkeypatch.setattr("runtime.messenger_vk_ui.ADMIN_IDS", [12345])
    labels = _vk_labels(vk_main_keyboard_json(12345))
    assert "🛠 Панель" in labels
    assert "admin" in _vk_commands(vk_main_keyboard_json(12345))


def test_vk_demo_kind_labels_match_telegram_demo_kind_surface():
    labels = _vk_labels(vk_demo_kind_keyboard_json())
    assert "🚗 Практика на утро / дорогу" in labels
    assert "🌙 Практика на вечер / домой" in labels
    assert "⬅️ Назад" in labels


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


def test_max_main_keyboard_uses_canonical_menu_titles():
    attachment = messenger_max_ui.main_menu_attachment()
    assert _max_button_texts(attachment) == [action.title for action in MAIN_MENU_ACTIONS]


def test_max_sender_delegates_main_keyboard_to_renderer():
    assert MaxBotSender._main_menu_attachment() == messenger_max_ui.main_menu_attachment()


def test_max_demo_kind_labels_match_telegram_demo_kind_surface():
    labels = _max_button_texts(messenger_max_ui.demo_kind_attachment())
    assert "🚗 Практика на утро / дорогу" in labels
    assert "🌙 Практика на вечер / домой" in labels
    assert "⬅️ Меню" in labels


def test_telegram_main_callbacks_are_tracked_by_contract():
    callbacks = [button.callback_data for row in kb_main().inline_keyboard for button in row]
    for callback in telegram_main_callbacks():
        assert callback in callbacks
