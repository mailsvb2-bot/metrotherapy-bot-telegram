from __future__ import annotations

import re
from typing import Any

from services.messenger.menu_contract import MAIN_MENU_ACTIONS, max_numbered_menu_text
from services.messenger.package_payment_ui import extract_labeled_urls
from runtime.telegram_button_parity import max_attachment_from_telegram
from keyboards.inline import (
    kb_after_post_actions,
    kb_delivery_channel_select,
    kb_delivery_channel_slots,
    kb_demo_kind,
    kb_full_access_menu,
    kb_main,
    kb_mood_done,
    kb_mood_scale,
    kb_ref_bonus_actions,
    kb_sales_offer,
    kb_settings_locked,
    kb_settings_menu,
    kb_state_period_menu,
    kb_state_rate_scale,
    kb_weather,
)

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
            or "полный маршрут" in compact
            or "кнопки max" in compact
            or "кнопки вконтакте" in compact
        )
    )


def main_menu_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_main(None))


def full_route_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_full_access_menu())


def demo_kind_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_demo_kind())


def weather_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_weather())


def weather_city_attachment() -> dict[str, Any]:
    return inline_keyboard_attachment([[max_message_button(BACK_LABEL, command=MENU_COMMAND)]])


def score_scale_attachment(session_id: int = 0, *, stage: str = "pre") -> dict[str, Any]:
    return max_attachment_from_telegram(kb_mood_scale(int(session_id), stage=str(stage or "pre")))


def post_audio_attachment(session_id: int = 0) -> dict[str, Any]:
    return max_attachment_from_telegram(kb_mood_done(int(session_id)))


def progress_attachment() -> dict[str, Any]:
    return state_period_attachment()


def settings_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_settings_menu())


def delivery_slots_attachment(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    return max_attachment_from_telegram(kb_delivery_channel_slots(snapshot or {"identities": [], "morning_channel": None, "evening_channel": None}))


def delivery_channel_select_attachment(slot: str = "morning", snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    slot = "evening" if str(slot).strip().lower() == "evening" else "morning"
    return max_attachment_from_telegram(kb_delivery_channel_select(slot, snapshot or {"identities": [], "morning_channel": None, "evening_channel": None}))


def state_period_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_state_period_menu())


def post_actions_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_after_post_actions())


def sales_offer_attachment() -> dict[str, Any]:
    return max_attachment_from_telegram(kb_sales_offer(0))


def full_access_attachment() -> dict[str, Any]:
    return full_route_attachment()


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
