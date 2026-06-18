from __future__ import annotations

import re
from typing import Any

from keyboards.inline import (
    kb_delivery_channel_select,
    kb_full_access_menu,
    kb_main,
    kb_ref_bonus_actions,
    kb_sales_offer,
    kb_settings_locked,
    kb_settings_menu,
    kb_state_period_menu,
    kb_state_rate_scale,
)
from runtime.telegram_button_parity import max_attachment_from_telegram
from services.messenger.menu_contract import max_numbered_menu_text
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
    return str(int(value))


def has_main_menu_text(text: str) -> bool:
    raw = str(text or "")
    head = raw.lstrip()[:500]
    compact = raw.casefold().replace("ё", "е")
    return (
        "Главное меню" in head
        and (
            "выберите маршрут" in compact
            or "попробовать бесплатно" in compact
            or "полный маршрут" in compact
            or "кнопки max" in compact
            or "кнопки вконтакте" in compact
        )
    )


def _replace_back_label(attachment: dict[str, Any], label: str = MAX_LEGACY_BACK_LABEL) -> dict[str, Any]:
    out = {"type": attachment.get("type", "inline_keyboard"), "payload": {"buttons": []}}
    for row in attachment.get("payload", {}).get("buttons", []):
        out_row = []
        for button in row:
            copied = dict(button)
            if copied.get("text") in {BACK_LABEL, "⬅️ Назад"} and (copied.get("payload") or {}).get("command") == MENU_COMMAND:
                copied["text"] = label
            out_row.append(copied)
        out["payload"]["buttons"].append(out_row)
    return out


def main_menu_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_main(None))


def full_route_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🎧 Получить аудио", command="continue")],
        [max_message_button("✅ Прослушал", command="done")],
        [max_message_button(MAX_LEGACY_BACK_LABEL, command=MENU_COMMAND)],
    ])


def demo_kind_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🚗 Практика на утро / дорогу", command="demo_work")],
        [max_message_button("🌙 Практика на вечер / домой", command="demo_home")],
        [max_message_button(MAX_LEGACY_BACK_LABEL, command=MENU_COMMAND)],
    ])


def weather_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🔄 Обновить погоду", command="weather")],
        [max_message_button("🏙 Изменить город", command="weather_city")],
        [max_message_button(MAX_LEGACY_BACK_LABEL, command=MENU_COMMAND)],
    ])


def weather_city_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([[max_message_button(BACK_LABEL, command=MENU_COMMAND)]])


def score_scale_attachment(session_id: int = 0, *, stage: str = "pre") -> dict[str, Any]:
    _ = session_id, stage
    values = list(range(-10, 11))
    rows: list[list[dict[str, Any]]] = []
    for i in range(0, len(values), 7):
        rows.append([max_message_button(_score_label(value), command=f"score:{value}") for value in values[i : i + 7]])
    rows.append([max_message_button("📈 Мой прогресс", command="progress")])
    rows.append([max_message_button(BACK_LABEL, command=MENU_COMMAND)])
    return inline_keyboard_attachment(rows)


def post_audio_attachment(session_id: int = 0) -> dict[str, Any]:
    _ = session_id
    return inline_keyboard_attachment([
        [max_message_button("✅ Прослушал", command="done")],
        [max_message_button(MAX_LEGACY_BACK_LABEL, command=MENU_COMMAND)],
    ])


def progress_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("🎧 Получить аудио", command="continue")],
        [max_message_button("✅ Прослушал", command="done")],
        [max_message_button("🔁 Повторить аудио", command="repeat")],
        [max_message_button("🧾 История", command="history")],
        [max_message_button(BACK_LABEL, command=MENU_COMMAND)],
    ])


def settings_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_settings_menu())


def delivery_slots_attachment(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = snapshot
    return inline_keyboard_attachment([
        [max_message_button("🌅 Утренние отправки", command="channel morning")],
        [max_message_button("🌙 Вечерние отправки", command="channel evening")],
        [max_message_button(BACK_LABEL, command="settings")],
    ])


def delivery_channel_select_attachment(slot: str = "morning", snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    _ = snapshot
    slot = "evening" if str(slot).strip().lower() == "evening" else "morning"
    return inline_keyboard_attachment([
        [max_message_button("♻️ Авто", command=f"channel {slot} auto")],
        [max_message_button("telegram", command=f"channel {slot} telegram")],
        [max_message_button("max", command=f"channel {slot} max")],
        [max_message_button("vk", command=f"channel {slot} vk")],
        [max_message_button(BACK_LABEL, command="time")],
    ])


def state_period_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_state_period_menu())


def post_actions_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([
        [max_message_button("📈 Посмотреть изменение состояния", command="progress")],
        [max_message_button("🔐 Открыть полный маршрут", command="pay")],
        [max_message_button("🎧 Ещё одна бесплатная практика", command="demo")],
        [max_message_button("🎁 Подарить подписку", command="gift")],
        [max_message_button(MAIN_MENU_LABEL, command=MENU_COMMAND)],
    ])


def sales_offer_attachment() -> dict[str, Any]:
    return _replace_back_label(max_attachment_from_telegram(kb_sales_offer(0)))


def full_access_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_full_access_menu())


def settings_locked_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_settings_locked())


def ref_bonus_actions_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_ref_bonus_actions())


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


def link_action_attachment(text: str) -> dict[str, Any] | None:
    rows = [[max_link_button(label, url)] for label, url in extract_labeled_urls(text)]
    if not rows:
        return None
    rows.append([max_message_button(BACK_LABEL, command=MENU_COMMAND)])
    return inline_keyboard_attachment(rows)


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
    if stripped.startswith("📨 Канал для утренних"):
        return [delivery_channel_select_attachment("morning")]
    if stripped.startswith("📨 Канал для вечерних"):
        return [delivery_channel_select_attachment("evening")]
    if stripped.startswith("🎁 Мои бонусы за приглашения"):
        return [ref_bonus_actions_attachment()]
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
    if raw.lstrip().startswith("🔐 Полный маршрут") and "в этот мессенджер" not in raw and "MAX и ВКонтакте" not in raw:
        raw = raw.rstrip() + "\n\nПолный маршрут доступен прямо в этот мессенджер."
    if has_main_menu_text(raw) and not has_native_keyboard and "отправьте:" not in raw:
        return raw.rstrip() + "\n\n" + max_numbered_menu_text()
    return raw


def state_rate_scale_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_state_rate_scale())


def attachment_for_reply_kind(kind: str | None, meta: dict[str, Any] | None = None) -> dict[str, Any] | None:
    meta = meta or {}
    if kind == "main":
        return main_menu_attachment()
    if kind == "demo_kind":
        return demo_kind_attachment()
    if kind == "score_scale":
        return score_scale_attachment(int(meta.get("session_id") or 0), stage=str(meta.get("stage") or "pre"))
    if kind == "weather":
        return weather_attachment()
    if kind == "weather_city":
        return weather_city_attachment()
    if kind == "progress":
        return progress_attachment()
    if kind == "settings":
        return settings_attachment()
    if kind == "delivery_slots":
        return delivery_slots_attachment()
    if kind == "delivery_morning":
        return delivery_channel_select_attachment("morning")
    if kind == "delivery_evening":
        return delivery_channel_select_attachment("evening")
    if kind == "state_period":
        return state_period_attachment()
    if kind == "state_rate":
        return state_rate_scale_attachment()
    if kind == "post_actions":
        return post_actions_attachment()
    if kind == "sales_offer":
        return sales_offer_attachment()
    if kind == "full_access":
        return full_access_attachment()
    if kind == "settings_locked":
        return settings_locked_attachment()
    if kind == "ref_bonus":
        return ref_bonus_actions_attachment()
    return None
