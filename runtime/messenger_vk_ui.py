from __future__ import annotations

import json
from typing import Any

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
)
from runtime.telegram_button_parity import canonical_button_command, vk_keyboard_from_telegram
from services.messenger.menu_contract import CONTEXT_ACTIONS, MAIN_MENU_ACTIONS, main_menu_commands
from services.messenger.package_payment_ui import extract_labeled_urls

BACK_LABEL = "⬅️ Назад"
MENU_LABEL = "⬅️ Меню"
HOME_LABEL = "🏠 Меню"
MAIN_MENU_LABEL = "⬅️ Главное меню"
MENU_COMMAND = "start"
VK_MAX_BUTTONS_PER_ROW = 5
VK_MAX_BUTTON_ROWS = 6


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
        },
        "color": "primary",
    }


def _keyboard(rows: list[list[dict[str, Any]]], *, inline: bool = False) -> str:
    return json.dumps({"one_time": False, "inline": inline, "buttons": rows}, ensure_ascii=False, separators=(",", ":"))


def _score_label(value: int) -> str:
    return f"{int(value):+d}" if int(value) != 0 else "0"


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
    aliases = {a.title.casefold().replace("ё", "е"): a.command for a in MAIN_MENU_ACTIONS + CONTEXT_ACTIONS}
    aliases.update({
        BACK_LABEL.casefold().replace("ё", "е"): MENU_COMMAND,
        MENU_LABEL.casefold().replace("ё", "е"): MENU_COMMAND,
        HOME_LABEL.casefold().replace("ё", "е"): MENU_COMMAND,
        MAIN_MENU_LABEL.casefold().replace("ё", "е"): MENU_COMMAND,
        "⬅️ меню": MENU_COMMAND,
        "⬅️ назад": MENU_COMMAND,
        "назад": MENU_COMMAND,
    })
    return aliases.get(label, "")


def telegram_main_parity_keyboard_json(keyboard_json: str) -> str:
    try:
        keyboard = json.loads(keyboard_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return keyboard_json
    if not isinstance(keyboard, dict) or not isinstance(keyboard.get("buttons"), list):
        return keyboard_json

    row_commands: list[tuple[list[Any], set[str]]] = []
    all_commands: set[str] = set()
    for row in keyboard["buttons"]:
        if not isinstance(row, list):
            row_commands.append((row, set()))
            continue
        commands = {button_command(button) for button in row}
        commands.discard("")
        all_commands.update(commands)
        row_commands.append((row, commands))

    if not set(main_menu_commands()).issubset(all_commands):
        return keyboard_json
    if not {"continue", "done"}.intersection(all_commands):
        return keyboard_json

    normalized = dict(keyboard)
    normalized["buttons"] = [row for row, commands in row_commands if not commands or not commands.issubset({"continue", "done"})]
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _snapshot(snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    return snapshot or {"identities": [], "morning_channel": None, "evening_channel": None}


def _looks_like_main_menu_text(text: str) -> bool:
    raw = str(text or "")
    head = raw.lstrip()[:700]
    compact = raw.casefold().replace("ё", "е")
    return "Главное меню" in head and (
        "выберите маршрут" in compact or "попробовать бесплатно" in compact or "полный маршрут" in compact
    )


def vk_payment_keyboard_json(text: str) -> str | None:
    rows = [[_open_link_button(label, url)] for label, url in extract_labeled_urls(text)]
    if not rows:
        return None
    rows.append([_button(BACK_LABEL, MENU_COMMAND, "secondary")])
    return _keyboard(rows, inline=True)


def prepare_vk_keyboard_json(keyboard_json: str, *, external_user_id: str, text: str) -> str:
    _ = external_user_id
    payment_keyboard = vk_payment_keyboard_json(text)
    if payment_keyboard is not None:
        return payment_keyboard
    if str(text or "").lstrip().startswith("🔐 Полный маршрут"):
        return full_route_keyboard_json()
    return telegram_main_parity_keyboard_json(keyboard_json)


def full_route_keyboard_json() -> str:
    return _keyboard([
        [_button("🎧 Получить аудио", "continue", "primary")],
        [_button("✅ Прослушал", "done", "positive")],
        [_button(BACK_LABEL, MENU_COMMAND, "secondary")],
    ])


def vk_main_keyboard_json(user_id: int | None = None) -> str:
    _ = user_id
    return vk_keyboard_from_telegram(kb_main(None))


def vk_default_keyboard_json() -> str:
    return vk_main_keyboard_json()


def vk_demo_kind_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_demo_kind())


def vk_weather_keyboard_json() -> str:
    return _keyboard([
        [_button("🌤 Погода", "weather", "primary")],
        [_button("🏙 Изменить город", "weather_city", "secondary")],
        [_button(BACK_LABEL, MENU_COMMAND, "secondary")],
    ])


def vk_weather_city_keyboard_json() -> str:
    return _keyboard([[_button(BACK_LABEL, MENU_COMMAND, "secondary")]])


def vk_score_scale_keyboard_json(session_id: int = 0, *, stage: str = "pre") -> str:
    _ = session_id, stage
    values = list(range(-10, 11))
    rows: list[list[dict[str, Any]]] = []
    for i in range(0, len(values), VK_MAX_BUTTONS_PER_ROW):
        chunk = values[i : i + VK_MAX_BUTTONS_PER_ROW]
        rows.append([_button(_score_label(value), str(value), "secondary") for value in chunk])
    # VK rejects inline keyboards with too many rows. Keep the 21-point score
    # scale intact, but place non-score controls into the final short score row.
    rows[-1].extend([
        _button("📈 Прогресс", "progress", "primary"),
        _button(BACK_LABEL, MENU_COMMAND, "secondary"),
    ])
    return _keyboard(rows)


def vk_progress_keyboard_json() -> str:
    return _keyboard([
        [_button("🎧 Получить аудио", "continue", "primary")],
        [_button("✅ Прослушал", "done", "positive")],
        [_button("🔁 Повторить аудио", "repeat", "secondary")],
        [_button("🧾 История", "history", "secondary")],
        [_button(BACK_LABEL, MENU_COMMAND, "secondary")],
    ])


def vk_post_audio_keyboard_json(session_id: int = 0) -> str:
    return vk_keyboard_from_telegram(kb_mood_done(int(session_id)))


def vk_settings_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_settings_menu())


def vk_delivery_slots_keyboard_json(snapshot: dict[str, Any] | None = None) -> str:
    _ = _snapshot(snapshot)
    return _keyboard([
        [_button("🌅 Утренние отправки", "channel morning", "secondary")],
        [_button("🌙 Вечерние отправки", "channel evening", "secondary")],
        [_button(BACK_LABEL, "settings", "secondary")],
    ])


def vk_delivery_channel_select_keyboard_json(slot: str = "morning", snapshot: dict[str, Any] | None = None) -> str:
    _ = _snapshot(snapshot)
    slot = "evening" if str(slot).strip().lower() == "evening" else "morning"
    return _keyboard([
        [_button("♻️ Авто", f"channel {slot} auto", "secondary")],
        [_button("telegram", f"channel {slot} telegram", "secondary")],
        [_button("max", f"channel {slot} max", "secondary")],
        [_button("vk", f"channel {slot} vk", "secondary")],
        [_button(BACK_LABEL, "time", "secondary")],
    ])


def vk_state_period_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_state_period_menu())


def vk_state_rate_scale_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_state_rate_scale())


def vk_post_actions_keyboard_json() -> str:
    return _keyboard([
        [_button("📈 Посмотреть изменение состояния", "progress", "primary")],
        [_button("🔐 Открыть полный маршрут", "pay", "primary")],
        [_button("🎧 Ещё одна бесплатная практика", "demo", "secondary")],
        [_button("🎁 Подарить подписку", "gift", "secondary")],
        [_button(MAIN_MENU_LABEL, MENU_COMMAND, "secondary")],
    ])


def vk_sales_offer_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_sales_offer(0))


def vk_full_access_keyboard_json() -> str:
    return vk_keyboard_from_telegram(kb_full_access_menu())


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
    elif _looks_like_main_menu_text(text):
        enriched.setdefault("keyboard_json", vk_main_keyboard_json(user_id))
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
    if kind == "main":
        return vk_main_keyboard_json()
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
    if kind == "post_audio":
        return vk_post_audio_keyboard_json(int(meta.get("session_id") or 0))
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
