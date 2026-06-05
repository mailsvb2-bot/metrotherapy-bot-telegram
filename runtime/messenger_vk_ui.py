from __future__ import annotations

import json
from typing import Any

from services.messenger.menu_contract import CONTEXT_ACTIONS, MAIN_MENU_ACTIONS, main_menu_commands
from services.messenger.package_payment_ui import extract_labeled_urls
from runtime.telegram_button_parity import vk_keyboard_from_telegram
from keyboards.inline import (
    kb_after_post_actions,
    kb_delivery_channel_select,
    kb_delivery_channel_slots,
    kb_demo_kind,
    kb_full_access_menu,
    kb_main,
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
MENU_LABEL = "⬅️ Меню"
HOME_LABEL = "🏠 Меню"
MAIN_MENU_LABEL = "⬅️ Главное меню"
MENU_COMMAND = "start"


def _button(label: str, command: str, color: str = "secondary") -> dict[str, Any]:
    return {
        "action": {
            "type": "text",
            "label": label,
            "payload": json.dumps({"command": command}, ensure_ascii=False),
        },
        "color": color,
    }


def _open_link_button(label: str, url: str) -> dict[str, Any]:
    return {
        "action": {
            "type": "open_link",
            "label": str(label or "Открыть")[:40],
            "link": str(url or ""),
            "payload": json.dumps({"url": str(url or "")}, ensure_ascii=False),
        }
    }


def _keyboard(rows: list[list[dict[str, Any]]], *, inline: bool = False) -> str:
    return json.dumps(
        {"one_time": False, "inline": inline, "buttons": rows},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _score_label(value: int) -> str:
    return f"{value:+d}" if value != 0 else "0"


def button_command(button: Any) -> str:
    if not isinstance(button, dict):
        return ""
    action = button.get("action") or {}
    payload = action.get("payload")
    if isinstance(payload, str) and payload.strip():
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            decoded = None
        if isinstance(decoded, dict):
            command = decoded.get("command") or decoded.get("cmd") or decoded.get("action")
            if isinstance(command, str) and command.strip():
                return command.strip()
    label = str(action.get("label") or "").strip().casefold().replace("ё", "е")
    label_aliases = {action.title.casefold().replace("ё", "е"): action.command for action in MAIN_MENU_ACTIONS + CONTEXT_ACTIONS}
    label_aliases[BACK_LABEL.casefold().replace("ё", "е")] = MENU_COMMAND
    label_aliases[MENU_LABEL.casefold().replace("ё", "е")] = MENU_COMMAND
    label_aliases[HOME_LABEL.casefold().replace("ё", "е")] = MENU_COMMAND
    label_aliases[MAIN_MENU_LABEL.casefold().replace("ё", "е")] = MENU_COMMAND
    label_aliases["⬅️ меню"] = MENU_COMMAND
    label_aliases["⬅️ назад"] = MENU_COMMAND
    label_aliases["назад"] = MENU_COMMAND
    return label_aliases.get(label, "")


def telegram_main_parity_keyboard_json(keyboard_json: str) -> str:
    try:
        keyboard = json.loads(keyboard_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return keyboard_json
    if not isinstance(keyboard, dict):
        return keyboard_json
    rows = keyboard.get("buttons")
    if not isinstance(rows, list):
        return keyboard_json

    all_commands: set[str] = set()
    row_commands: list[tuple[list[Any], set[str]]] = []
    for row in rows:
        if not isinstance(row, list):
            row_commands.append((row, set()))
            continue
        commands = {button_command(button) for button in row}
        commands.discard("")
        all_commands.update(commands)
        row_commands.append((row, commands))

    telegram_main_commands = set(main_menu_commands())
    vk_only_main_controls = {"continue", "done"}
    if not telegram_main_commands.issubset(all_commands):
        return keyboard_json
    if not vk_only_main_controls.intersection(all_commands):
        return keyboard_json

    filtered_rows = [row for row, commands in row_commands if not commands or not commands.issubset(vk_only_main_controls)]
    normalized = dict(keyboard)
    normalized["buttons"] = filtered_rows
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))




def full_route_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_full_access_menu())

def vk_payment_keyboard_json(text: str) -> str | None:
    # Payment/gift/share link keyboards are intentionally closed for VK until
    # they have explicit Telegram-parity coverage. URLs remain in message text.
    return None

def prepare_vk_keyboard_json(keyboard_json: str, *, external_user_id: str, text: str) -> str:
    payment_keyboard = vk_payment_keyboard_json(text)
    if payment_keyboard is not None:
        return payment_keyboard
    if (text or "").lstrip().startswith("🔐 Полный маршрут"):
        return full_route_keyboard_json()
    return telegram_main_parity_keyboard_json(keyboard_json)



def vk_main_keyboard_json(user_id: int | None = None) -> str:
    _ = user_id
    return vk_keyboard_from_telegram(kb_main(None))


def vk_default_keyboard_json() -> str:
    return vk_main_keyboard_json()


def vk_demo_kind_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_demo_kind())


def vk_weather_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_weather())

def vk_weather_city_keyboard_json() -> str:
    """VK keyboard while waiting for city input."""
    return _keyboard([[_button(BACK_LABEL, MENU_COMMAND, "secondary")]])




def vk_score_scale_keyboard_json(session_id: int = 0, *, stage: str = "pre") -> str:
    return vk_keyboard_from_telegram(kb_mood_scale(int(session_id), stage=str(stage or "pre")))


def vk_progress_keyboard_json() -> str:
    return vk_state_period_keyboard_json()


def vk_settings_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_settings_menu())


def vk_delivery_slots_keyboard_json(snapshot: dict[str, Any] | None = None) -> str:
    return vk_keyboard_from_telegram(kb_delivery_channel_slots(snapshot or {"identities": [], "morning_channel": None, "evening_channel": None}))


def vk_delivery_channel_select_keyboard_json(slot: str = "morning", snapshot: dict[str, Any] | None = None) -> str:
    slot = "evening" if str(slot).strip().lower() == "evening" else "morning"
    return vk_keyboard_from_telegram(kb_delivery_channel_select(slot, snapshot or {"identities": [], "morning_channel": None, "evening_channel": None}))


def vk_state_period_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_state_period_menu())


def vk_post_actions_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_after_post_actions())


def vk_sales_offer_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_sales_offer(0))


def vk_full_access_keyboard_json() -> str:
    return full_route_keyboard_json()


def vk_settings_locked_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_settings_locked())


def vk_ref_bonus_actions_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_ref_bonus_actions())

def vk_text_send_kwargs(platform: str, text: str = "", *, user_id: int | None = None) -> dict[str, Any]:
    if platform != "vk":
        return {}
    payment_keyboard = vk_payment_keyboard_json(text)
    if payment_keyboard is not None:
        return {"keyboard_json": payment_keyboard}
    return {"keyboard_json": vk_main_keyboard_json(user_id)}



def with_vk_keyboard(platform: str, kwargs: dict[str, Any], *, user_id: int | None = None) -> dict[str, Any]:
    if platform != "vk":
        return kwargs
    enriched = dict(kwargs)
    text = str(enriched.pop("_text_for_keyboard", "") or "")
    payment_keyboard = vk_payment_keyboard_json(text)
    if payment_keyboard is not None:
        enriched["keyboard_json"] = payment_keyboard
    elif text.lstrip().startswith("🎧 Общий прогресс") or text.lstrip().startswith("🎧 Вы ещё не запускали") or "📈 Мой прогресс" in text or "📈 Анализ состояния" in text:
        enriched.setdefault("keyboard_json", vk_progress_keyboard_json())
    elif text.lstrip().startswith("⚙️ Настройки канала"):
        enriched.setdefault("keyboard_json", vk_settings_keyboard_json())
    elif text.lstrip().startswith("🕒 Правила отправки") or "Каналы по времени дня" in text:
        enriched.setdefault("keyboard_json", vk_delivery_slots_keyboard_json())
    elif text.lstrip().startswith("📨 Канал для утренних"):
        enriched.setdefault("keyboard_json", vk_delivery_channel_select_keyboard_json("morning"))
    elif text.lstrip().startswith("📨 Канал для вечерних"):
        enriched.setdefault("keyboard_json", vk_delivery_channel_select_keyboard_json("evening"))
    elif text.lstrip().startswith("🎁 Мои бонусы за приглашения"):
        enriched.setdefault("keyboard_json", vk_ref_bonus_actions_keyboard_json())
    else:
        enriched.setdefault("keyboard_json", vk_main_keyboard_json(user_id))
    return enriched


def keyboard_for_reply_kind(kind: str | None, meta: dict[str, Any] | None = None) -> str | None:
    meta = meta or {}
    if kind == "demo_kind":
        return vk_demo_kind_keyboard_json()
    if kind == "score_scale":
        return vk_score_scale_keyboard_json(int(meta.get("session_id") or 0), stage=str(meta.get("stage") or "pre"))
    if kind == "weather":
        return vk_weather_keyboard_json()
    if kind == "weather_city":
        return vk_weather_city_keyboard_json()
    if kind == "progress":
        return vk_progress_keyboard_json()
    if kind == "settings":
        return vk_settings_keyboard_json()
    if kind == "delivery_slots":
        return vk_delivery_slots_keyboard_json()
    if kind == "delivery_morning":
        return vk_delivery_channel_select_keyboard_json("morning")
    if kind == "delivery_evening":
        return vk_delivery_channel_select_keyboard_json("evening")
    if kind == "state_period":
        return vk_state_period_keyboard_json()
    if kind == "state_rate":
        return vk_state_rate_scale_keyboard_json()
    if kind == "post_actions":
        return vk_post_actions_keyboard_json()
    if kind == "sales_offer":
        return vk_sales_offer_keyboard_json()
    if kind == "full_access":
        return vk_full_access_keyboard_json()
    if kind == "settings_locked":
        return vk_settings_locked_keyboard_json()
    if kind == "ref_bonus":
        return vk_ref_bonus_actions_keyboard_json()
    return None

def vk_state_rate_scale_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_state_rate_scale())
