from __future__ import annotations

import re
from typing import Any

from services.messenger.menu_contract import MAIN_MENU_ACTIONS, max_numbered_menu_text
from services.messenger.package_payment_ui import extract_labeled_urls


def max_message_button(text: str, *, command: str | None = None) -> dict[str, Any]:
    button: dict[str, Any] = {"type": "message", "text": text}
    if command is not None:
        button["payload"] = {"command": command}
    return button


def max_link_button(text: str, url: str) -> dict[str, str]:
    return {"type": "link", "text": text, "url": url}


def inline_keyboard_attachment(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
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
    rows: list[list[dict[str, Any]]] = []
    actions = list(MAIN_MENU_ACTIONS)
    for idx in range(0, len(actions), 2):
        rows.append([max_message_button(action.title, command=action.command) for action in actions[idx:idx + 2]])
    return inline_keyboard_attachment(rows)


def full_route_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🎧 Получить аудио", command="continue"), max_message_button("✅ Прослушал", command="done")],
        [max_message_button("⬅️ Меню", command="start")],
    ])


def demo_kind_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🚗 Практика на утро / дорогу", command="demo_work")],
        [max_message_button("🌙 Практика на вечер / домой", command="demo_home")],
        [max_message_button("⬅️ Меню", command="start")],
    ])


def weather_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🔄 Обновить погоду", command="weather"), max_message_button("🏙 Изменить город", command="weather_city")],
        [max_message_button("⬅️ Меню", command="start")],
    ])


def weather_city_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([[max_message_button("⬅️ Меню", command="start")]])


def score_scale_attachment() -> dict[str, Any]:
    """MAX score scale with unambiguous numeric payloads.

    Bare commands "1" and "2" are already legacy aliases for demo route choices.
    Score buttons therefore use the provider payload shape "score:<number>".
    runtime.messenger_payloads.normalise_messenger_text converts that payload
    back to the canonical score string before the mood flow sees it.
    """
    rows: list[list[dict[str, Any]]] = []
    for row in [[-10, -9, -8], [-7, -6, -5], [-4, -3, -2], [-1, 0, 1], [2, 3, 4], [5, 6, 7], [8, 9, 10]]:
        rows.append([
            max_message_button(str(value), command=f"score:{value}")
            for value in row
        ])
    rows.append([
        max_message_button("📈 Мой прогресс", command="progress"),
        max_message_button("⬅️ Меню", command="start"),
    ])
    return inline_keyboard_attachment(rows)


def post_audio_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("✅ Прослушал", command="done")],
        [max_message_button("📈 Мой прогресс", command="progress"), max_message_button("🧾 История", command="history")],
        [max_message_button("⬅️ Меню", command="start")],
    ])


def progress_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🎧 Получить аудио", command="continue"), max_message_button("✅ Прослушал", command="done")],
        [max_message_button("🧾 История", command="history"), max_message_button("⬅️ Меню", command="start")],
    ])


def settings_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("/platform telegram", command="/platform telegram"), max_message_button("/platform max", command="/platform max"), max_message_button("/platform vk", command="/platform vk")],
        [max_message_button("switch", command="switch"), max_message_button("⬅️ Меню", command="start")],
    ])


def is_score_scale_text(text: str) -> bool:
    raw = str(text or "").casefold().replace("−", "-")
    return "-10" in raw and "10" in raw and ("шкал" in raw or "оцен" in raw or "состояни" in raw)


def is_post_audio_controls_text(text: str) -> bool:
    raw = str(text or "").casefold().replace("ё", "е")
    listened_marker = "прослуш" in raw or "дослуш" in raw
    done_marker = "done" in raw or "готово" in raw or "прослушал" in raw or "дослушал" in raw
    audio_marker = "аудио" in raw or "транс" in raw or "файл" in raw
    return listened_marker and done_marker and audio_marker


def first_url(text: str) -> str:
    match = re.search(r"https?://[^\s)]+", text or "")
    return match.group(0).rstrip(".,;") if match else ""


def _labeled_link_rows(text: str) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    for label, url in extract_labeled_urls(text):
        if label != "Открыть":
            rows.append([max_link_button(label, url)])
    return rows


def link_action_attachment(text: str) -> dict[str, Any] | None:
    raw = str(text or "")
    url = first_url(raw)
    if not url:
        return None
    if raw.lstrip().startswith("💳"):
        rows = _labeled_link_rows(raw) or [[max_link_button("💳 Оплатить", url)]]
        rows.append([max_message_button("🎧 Получить аудио", command="continue"), max_message_button("⬅️ Меню", command="start")])
        return inline_keyboard_attachment(rows)
    if raw.lstrip().startswith("🎁"):
        rows = _labeled_link_rows(raw) or [[max_link_button("🎁 Оплатить подарок", url)]]
        rows.append([max_message_button("📣 Посоветовать", command="share"), max_message_button("⬅️ Меню", command="start")])
        return inline_keyboard_attachment(rows)
    if raw.lstrip().startswith("↗️ Поделиться"):
        return inline_keyboard_attachment([
            [max_link_button("↗️ Открыть ссылку", url)],
            [max_message_button("⬅️ Меню", command="start")],
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
    raw = str(text or "")
    replacements = {
        "Кнопки ВКонтакте соответствуют": "Кнопки MAX и ВКонтакте соответствуют",
        "Telegram для этого не нужен — сценарий исполняется внутри ВКонтакте.": "Telegram для этого не нужен — сценарий исполняется прямо в этот мессенджер.",
        "Во ВКонтакте маршрут исполняется через ту же общую аудио-очередь": "В этот мессенджер маршрут исполняется через ту же общую аудио-очередь",
        "продолжить полный маршрут во ВКонтакте": "продолжить полный маршрут в этот мессенджер",
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
