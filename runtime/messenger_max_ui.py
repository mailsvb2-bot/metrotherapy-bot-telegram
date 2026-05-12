from __future__ import annotations

import re
from typing import Any

from services.messenger.menu_contract import MAIN_MENU_ACTIONS, max_numbered_menu_text


def max_message_button(text: str) -> dict[str, str]:
    return {"type": "message", "text": text}


def max_link_button(text: str, url: str) -> dict[str, str]:
    return {"type": "link", "text": text, "url": url}


def inline_keyboard_attachment(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def has_main_menu_text(text: str) -> bool:
    raw = str(text or "")
    head = raw.lstrip()[:500]
    compact = raw.casefold().replace("ё", "е")
    return (
        "Главное меню" in head
        and (
            "выберите маршрут" in compact
            or "попробовать бесплатно" in compact
            or "кнопки max" in compact
            or "кнопки вконтакте" in compact
        )
    )


def main_menu_attachment() -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    actions = list(MAIN_MENU_ACTIONS)
    for idx in range(0, len(actions), 2):
        rows.append([max_message_button(action.title) for action in actions[idx:idx + 2]])
    return inline_keyboard_attachment(rows)


def full_route_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🎧 Получить аудио"), max_message_button("✅ Прослушал")],
        [max_message_button("⬅️ Меню")],
    ])


def demo_kind_attachment() -> dict[str, Any]:
    """MAX keyboard for Telegram demo-kind parity."""
    return inline_keyboard_attachment([
        [max_message_button("🚗 Практика на утро / дорогу")],
        [max_message_button("🌙 Практика на вечер / домой")],
        [max_message_button("⬅️ Меню")],
    ])


def weather_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🔄 Обновить погоду"), max_message_button("🏙 Изменить город")],
        [max_message_button("⬅️ Меню")],
    ])


def weather_city_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([[max_message_button("⬅️ Меню")]])


def score_scale_attachment() -> dict[str, Any]:
    rows: list[list[dict[str, str]]] = []
    for row in [[-10, -9, -8], [-7, -6, -5], [-4, -3, -2], [-1, 0, 1], [2, 3, 4], [5, 6, 7], [8, 9, 10]]:
        rows.append([max_message_button(str(value)) for value in row])
    rows.append([max_message_button("📈 Мой прогресс"), max_message_button("⬅️ Меню")])
    return inline_keyboard_attachment(rows)


def post_audio_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("✅ Прослушал")],
        [max_message_button("📊 Прогресс"), max_message_button("🧾 История")],
        [max_message_button("⬅️ Меню")],
    ])


def progress_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🎧 Получить аудио"), max_message_button("✅ Прослушал")],
        [max_message_button("🧾 История"), max_message_button("⬅️ Меню")],
    ])


def settings_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("/platform telegram"), max_message_button("/platform max"), max_message_button("/platform vk")],
        [max_message_button("switch"), max_message_button("⬅️ Меню")],
    ])


def is_score_scale_text(text: str) -> bool:
    raw = str(text or "").casefold().replace("−", "-")
    return "-10" in raw and "10" in raw and ("шкал" in raw or "оцен" in raw or "состояни" in raw)


def is_post_audio_controls_text(text: str) -> bool:
    raw = str(text or "").casefold().replace("ё", "е")
    return "прослуш" in raw and ("когда дослушаете" in raw or "когда прослушаете" in raw or "аудио" in raw) and ("done" in raw or "готово" in raw or "прослушал" in raw)


def first_url(text: str) -> str:
    match = re.search(r"https?://[^\s)]+", text or "")
    return match.group(0).rstrip(".,;") if match else ""


def link_action_attachment(text: str) -> dict[str, Any] | None:
    url = first_url(text)
    if not url:
        return None
    if str(text or "").lstrip().startswith("💳 Оплата"):
        return inline_keyboard_attachment([
            [max_link_button("💳 Оплатить", url)],
            [max_message_button("🎧 Получить аудио"), max_message_button("⬅️ Меню")],
        ])
    if str(text or "").lstrip().startswith("🎁 Подарить"):
        return inline_keyboard_attachment([
            [max_link_button("🎁 Оплатить подарок", url)],
            [max_message_button("📣 Посоветовать"), max_message_button("⬅️ Меню")],
        ])
    if str(text or "").lstrip().startswith("↗️ Поделиться"):
        return inline_keyboard_attachment([
            [max_link_button("↗️ Открыть ссылку", url)],
            [max_message_button("⬅️ Меню")],
        ])
    return None


def native_keyboard_attachments(text: str) -> list[dict[str, Any]]:
    raw = str(text or "")
    stripped = raw.lstrip()
    link_attachment = link_action_attachment(raw)
    if link_attachment is not None:
        return [link_attachment]
    if has_main_menu_text(raw):
        return [main_menu_attachment()]
    if stripped.startswith("🌿 Бесплатная практика"):
        return [demo_kind_attachment()]
    if stripped.startswith("🔐 Полный маршрут"):
        return [full_route_attachment()]
    if stripped.startswith("🌤 Погода") or "🏙 Изменить город" in raw:
        return [weather_attachment()]
    if stripped.startswith("🏙 Напишите название города"):
        return [weather_city_attachment()]
    if is_score_scale_text(raw):
        return [score_scale_attachment()]
    if is_post_audio_controls_text(raw):
        return [post_audio_attachment()]
    if stripped.startswith("🎧 Общий прогресс") or "📈 Анализ состояния" in raw:
        return [progress_attachment()]
    if stripped.startswith("⚙️ Настройки канала"):
        return [settings_attachment()]
    return []


def normalize_max_text(text: str) -> str:
    """Remove VK-only wording from shared MAX/VK text surfaces.

    The canonical text UI is shared by non-Telegram messengers. VK-specific
    execution details are acceptable for VK, but MAX must not tell the user that
    the scenario happens only inside VK.
    """
    raw = str(text or "")
    replacements = {
        "Кнопки ВКонтакте соответствуют": "Кнопки MAX и ВКонтакте соответствуют",
        "Telegram для этого не нужен — сценарий исполняется внутри ВКонтакте.": "Telegram для этого не нужен — сценарий исполняется прямо в этом мессенджере.",
        "Во ВКонтакте маршрут исполняется через ту же общую аудио-очередь": "В этом мессенджере маршрут исполняется через ту же общую аудио-очередь",
        "продолжить полный маршрут во ВКонтакте": "продолжить полный маршрут в этом мессенджере",
        "Во ВКонтакте доступны те же базовые действия": "В MAX и ВКонтакте доступны те же базовые действия",
        "После выбора оценки аудио придёт прямо во ВКонтакте.": "После выбора оценки аудио придёт прямо в этот мессенджер.",
        "В VK нельзя надёжно открыть системный выбор друзей прямо из кнопки бота.": "В MAX/VK нельзя надёжно открыть системный выбор друзей прямо из кнопки бота.",
        "в VK, Telegram или любом другом мессенджере": "в MAX, VK, Telegram или любом другом мессенджере",
        "Кнопки ниже открывают VK-бота, Telegram и сайт.": "Кнопки ниже открывают доступные каналы и сайт.",
        "Позже можно усилить это до полноценного выбора друга внутри VK": "Позже можно усилить это до полноценного выбора друга внутри мессенджера",
    }
    for src, dst in replacements.items():
        raw = raw.replace(src, dst)
    return raw


def prepare_text(text: str, *, has_native_keyboard: bool = False) -> str:
    raw = normalize_max_text(text)
    if has_main_menu_text(raw) and not has_native_keyboard and "отправьте:" not in raw:
        return raw.rstrip() + "\n\n" + max_numbered_menu_text()
    return raw
