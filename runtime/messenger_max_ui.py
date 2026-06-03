from __future__ import annotations

import re
from typing import Any

from services.messenger.menu_contract import MAIN_MENU_ACTIONS, max_numbered_menu_text
from services.messenger.package_payment_ui import extract_labeled_urls

BACK_LABEL = "⬅️ Назад"
MAX_LEGACY_BACK_LABEL = "⬅️ Меню"
HOME_LABEL = "🏠 Меню"
MAIN_MENU_LABEL = "⬅️ Главное меню"
MENU_COMMAND = "start"


def max_message_button(text: str, *, command: str | None = None) -> dict[str, Any]:
    button: dict[str, Any] = {"type": "message", "text": text}
    if command is not None:
        button["payload"] = {"command": command}
    return button


def max_link_button(text: str, url: str) -> dict[str, str]:
    return {"type": "link", "text": text, "url": url}


def inline_keyboard_attachment(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}



def _score_label(value: int) -> str:
    return f"{int(value):+d}" if int(value) != 0 else "0"

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
    """MAX main menu rendered from the Telegram-derived public user contract.

    Admin/control-plane buttons intentionally stay Telegram-only.
    """
    rows: list[list[dict[str, Any]]] = []
    actions = list(MAIN_MENU_ACTIONS)
    for idx in range(0, len(actions), 2):
        rows.append([max_message_button(action.title, command=action.command) for action in actions[idx:idx + 2]])
    return inline_keyboard_attachment(rows)



def full_route_attachment() -> dict[str, Any]:
    # Telegram kb_full_access_menu parity.
    return inline_keyboard_attachment([
        [max_message_button("🔐 Открыть полный маршрут", command="pay")],
        [max_message_button("⏰ Напомнить завтра утром", command="remind_continue_tomorrow")],
        [max_message_button(BACK_LABEL, command=MENU_COMMAND)],
    ])

def demo_kind_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🚗 Практика на утро / дорогу", command="demo_work")],
        [max_message_button("🌙 Практика на вечер / домой", command="demo_home")],
        [max_message_button(BACK_LABEL, command=MENU_COMMAND)],
    ])



def weather_attachment() -> dict[str, Any]:
    # Telegram kb_weather parity: no invented refresh button.
    return inline_keyboard_attachment([
        [max_message_button("🏙 Изменить город", command="weather_city")],
        [max_message_button(BACK_LABEL, command=MENU_COMMAND)],
    ])

def weather_city_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([[max_message_button(BACK_LABEL, command=MENU_COMMAND)]])



def score_scale_attachment() -> dict[str, Any]:
    # Telegram kb_mood_scale parity: -10..10, rows of seven, then menu.
    rows: list[list[dict[str, Any]]] = []
    vals = list(range(-10, 11))
    for idx in range(0, len(vals), 7):
        rows.append([
            max_message_button(_score_label(value), command=f"score:{value}")
            for value in vals[idx:idx + 7]
        ])
    rows.append([max_message_button(MAX_LEGACY_BACK_LABEL, command=MENU_COMMAND)])
    return inline_keyboard_attachment(rows)


def post_audio_attachment() -> dict[str, Any]:
    # Telegram kb_mood_done parity.
    return inline_keyboard_attachment([
        [max_message_button("✅ Прослушал", command="done")],
        [max_message_button(MAX_LEGACY_BACK_LABEL, command=MENU_COMMAND)],
    ])


def progress_attachment() -> dict[str, Any]:
    # Telegram state-period surface parity, not a second audio-control menu.
    return state_period_attachment()


def settings_attachment() -> dict[str, Any]:
    # Closed until full Telegram 1:1 settings subtree is certified.
    return main_menu_attachment()


def delivery_slots_attachment() -> dict[str, Any]:
    # Closed until full Telegram 1:1 delivery subtree is certified.
    return main_menu_attachment()


def delivery_channel_select_attachment(slot: str = "morning") -> dict[str, Any]:
    # Closed until full Telegram 1:1 delivery channel selector is certified.
    return main_menu_attachment()

def state_period_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("⭐ Оценить состояние сейчас", command="continue")],
        [max_message_button("📅 Сегодня", command="progress"), max_message_button("📆 Вчера", command="history")],
        [max_message_button("🗓 За всё время", command="progress")],
        [max_message_button("🔐 Открыть полный маршрут", command="pay"), max_message_button("🎁 Подарить", command="gift")],
        [max_message_button(MAX_LEGACY_BACK_LABEL, command=MENU_COMMAND)],
    ])



def post_actions_attachment() -> dict[str, Any]:
    # Closed until this post-audio surface is included in strict parity tests.
    return main_menu_attachment()


def sales_offer_attachment() -> dict[str, Any]:
    # Closed until sales offer surface is certified against Telegram.
    return main_menu_attachment()

def full_access_attachment() -> dict[str, Any]:
    return full_route_attachment()


def settings_locked_attachment() -> dict[str, Any]:
    # Covered fallback: full access menu already has Telegram parity.
    return full_route_attachment()


def ref_bonus_actions_attachment() -> dict[str, Any]:
    # Closed until referral bonus actions are certified against Telegram.
    return main_menu_attachment()

def is_score_scale_text(text: str) -> bool:
    raw = str(text or "").casefold().replace("−", "-")
    return "-10" in raw and "10" in raw and ("шкал" in raw or "оцен" in raw or "состояни" in raw)


def is_post_audio_controls_text(text: str) -> bool:
    raw = str(text or "").casefold().replace("ё", "е")
    listened_marker = "прослуш" in raw or "дослуш" in raw
    done_marker = "done" in raw or "готово" in raw or "прослушал" in raw or "дослушал"
    audio_marker = "аудио" in raw or "транс" in raw or "файл"
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
    # Payment/gift/share link keyboards are intentionally closed for MAX until
    # they have explicit Telegram-parity coverage. URLs remain in message text.
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
    if stripped.startswith("🎧 Общий прогресс") or stripped.startswith("🎧 Вы ещё не запускали") or "📈 Мой прогресс" in raw or "📈 Анализ состояния" in raw:
        return [progress_attachment()]
    if stripped.startswith("⚙️ Настройки канала"):
        return [settings_attachment()]
    if stripped.startswith("🕒 Правила отправки") or "Каналы по времени дня" in raw:
        return [delivery_slots_attachment()]
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
