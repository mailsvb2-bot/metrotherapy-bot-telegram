import json

from runtime import messenger_max_ui
from runtime.messenger_vk_ui import (
    vk_delivery_channel_select_keyboard_json,
    vk_delivery_slots_keyboard_json,
    vk_full_access_keyboard_json,
    vk_post_actions_keyboard_json,
    vk_ref_bonus_actions_keyboard_json,
    vk_sales_offer_keyboard_json,
    vk_settings_keyboard_json,
    vk_settings_locked_keyboard_json,
    vk_state_period_keyboard_json,
)


def _vk_labels(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    return [button["action"]["label"] for row in keyboard["buttons"] for button in row]


def _vk_commands(keyboard_json: str) -> list[str]:
    keyboard = json.loads(keyboard_json)
    out: list[str] = []
    for row in keyboard["buttons"]:
        for button in row:
            out.append(json.loads(button["action"]["payload"])["command"])
    return out


def _max_labels(attachment: dict) -> list[str]:
    return [button["text"] for row in attachment["payload"]["buttons"] for button in row]


def _max_commands(attachment: dict) -> list[str]:
    out: list[str] = []
    for row in attachment["payload"]["buttons"]:
        for button in row:
            out.append((button.get("payload") or {}).get("command") or "")
    return out


def test_deep_settings_surface_matches_telegram_public_settings_actions():
    expected_labels = [
        "🌦 Погода в моём городе",
        "⏰ Время: дорога на работу",
        "⏰ Время: дорога домой",
        "🎁 Мои бонусы за приглашения",
        "💬 Предпочтительный мессенджер",
        "📨 Каналы по времени дня",
        "📈 Анализ моего состояния",
        "⬅️ Назад",
    ]
    assert _vk_labels(vk_settings_keyboard_json()) == expected_labels
    assert _max_labels(messenger_max_ui.settings_attachment()) == expected_labels
    assert "admin" not in _vk_commands(vk_settings_keyboard_json())
    assert "admin" not in _max_commands(messenger_max_ui.settings_attachment())


def test_delivery_channel_surfaces_exist_for_vk_and_max():
    expected_slot_labels = ["🌅 Утренние отправки", "🌙 Вечерние отправки", "⬅️ Назад"]
    assert _vk_labels(vk_delivery_slots_keyboard_json()) == expected_slot_labels
    assert _max_labels(messenger_max_ui.delivery_slots_attachment()) == expected_slot_labels

    for slot in ["morning", "evening"]:
        expected_select_labels = ["♻️ Авто", "telegram", "max", "vk", "⬅️ Назад"]
        vk_commands = _vk_commands(vk_delivery_channel_select_keyboard_json(slot))
        max_commands = _max_commands(messenger_max_ui.delivery_channel_select_attachment(slot))
        assert _vk_labels(vk_delivery_channel_select_keyboard_json(slot)) == expected_select_labels
        assert _max_labels(messenger_max_ui.delivery_channel_select_attachment(slot)) == expected_select_labels
        assert f"channel {slot} auto" in vk_commands
        assert f"channel {slot} telegram" in vk_commands
        assert f"channel {slot} max" in max_commands
        assert f"channel {slot} vk" in max_commands


def test_state_period_surface_matches_telegram_public_state_actions():
    expected_labels = [
        "⭐ Оценить состояние сейчас",
        "📅 Сегодня",
        "📆 Вчера",
        "🗓 За всё время",
        "🔐 Открыть полный маршрут",
        "🎁 Подарить",
        "⬅️ Меню",
    ]
    assert _vk_labels(vk_state_period_keyboard_json()) == expected_labels
    assert _max_labels(messenger_max_ui.state_period_attachment()) == expected_labels


def test_post_actions_surface_matches_telegram_public_post_actions():
    expected_labels = [
        "📈 Посмотреть изменение состояния",
        "🔐 Открыть полный маршрут",
        "🎧 Ещё одна бесплатная практика",
        "🎁 Подарить подписку",
        "⬅️ Главное меню",
    ]
    assert _vk_labels(vk_post_actions_keyboard_json()) == expected_labels
    assert _max_labels(messenger_max_ui.post_actions_attachment()) == expected_labels


def test_sales_full_locked_and_ref_surfaces_match_for_vk_and_max():
    sales_labels = ["🔐 Открыть полный маршрут", "🎧 Ещё одна бесплатная практика", "🎁 Подарить подписку другу", "⬅️ Меню"]
    assert _vk_labels(vk_sales_offer_keyboard_json()) == sales_labels
    assert _max_labels(messenger_max_ui.sales_offer_attachment()) == sales_labels

    full_access_labels = ["🔐 Открыть полный маршрут", "⏰ Напомнить завтра утром", "⬅️ Назад"]
    assert _vk_labels(vk_full_access_keyboard_json()) == full_access_labels
    assert _max_labels(messenger_max_ui.full_access_attachment()) == full_access_labels

    locked_labels = ["🔐 Открыть полный маршрут", "🎁 Передать ритм", "⬅️ Назад"]
    assert _vk_labels(vk_settings_locked_keyboard_json()) == locked_labels
    assert _max_labels(messenger_max_ui.settings_locked_attachment()) == locked_labels

    ref_labels = ["🔐 Открыть полный маршрут", "🎁 Подарить подписку другу", "📈 Анализ моего состояния", "⬅️ Назад"]
    assert _vk_labels(vk_ref_bonus_actions_keyboard_json()) == ref_labels
    assert _max_labels(messenger_max_ui.ref_bonus_actions_attachment()) == ref_labels
