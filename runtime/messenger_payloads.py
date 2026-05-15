from __future__ import annotations

import hashlib
import json
from typing import Any

from services.messenger.menu_contract import normalize_menu_command


def _score_command_value(value: str) -> str | None:
    raw = str(value or "").strip().casefold().replace("−", "-")
    if raw.startswith("score:"):
        candidate = raw.split(":", 1)[1].strip()
    elif raw.startswith("score="):
        candidate = raw.split("=", 1)[1].strip()
    else:
        return None
    if candidate.startswith("+"):
        candidate = candidate[1:]
    try:
        score = int(candidate)
    except ValueError:
        return None
    if -10 <= score <= 10:
        return str(score)
    return None


def _plain_score_value(value: str) -> str | None:
    """Normalize plain score text without breaking legacy demo aliases.

    MAX may send the visible button text instead of payload.command. Negative
    scores such as "-5" must therefore be accepted before menu normalization;
    otherwise they can be interpreted as a menu reset. Bare "1" and "2" remain
    legacy route aliases, while MAX score buttons render them as "+1" and "+2".
    """
    raw = str(value or "").strip().casefold().replace("−", "-")
    if not raw:
        return None
    if raw.startswith("+") and raw[1:].isdigit():
        candidate = raw[1:]
    elif raw.startswith("-") and raw[1:].isdigit():
        candidate = raw
    elif raw == "0" or raw in {"3", "4", "5", "6", "7", "8", "9", "10"}:
        candidate = raw
    else:
        return None
    try:
        score = int(candidate)
    except ValueError:
        return None
    if -10 <= score <= 10:
        return str(score)
    return None


def normalise_messenger_text(text: str) -> str:
    """Normalize human/mobile button labels to canonical text commands.

    VK and MAX buttons send plain text labels. Telegram remains the source of
    truth for those labels through ``services.messenger.menu_contract``; this
    function only keeps route/context aliases that are outside the Telegram main
    menu, such as demo route choice, weather city and score buttons.
    """
    raw = (text or "").strip()
    score_command = _score_command_value(raw)
    if score_command is not None:
        return score_command
    plain_score = _plain_score_value(raw)
    if plain_score is not None:
        return plain_score

    compact = raw.casefold().replace("ё", "е")
    compact = " ".join(compact.split())

    command = normalize_menu_command(compact)
    if command:
        return "start" if command == "start" else command

    aliases = {
        "/start": "start",
        "start": "start",
        "старт": "start",
        "начать": "start",
        "🌿 начать": "start",
        "menu": "start",
        "/menu": "start",
        "меню": "start",
        "главное меню": "start",
        "⬅️ назад": "start",
        "назад": "start",
        "⬅️ меню": "start",
        "1": "demo_work",
        "1.": "demo_work",
        "1️⃣": "demo_work",
        "1️⃣ утро / дорога": "demo_work",
        "утро / дорога": "demo_work",
        "утро": "demo_work",
        "дорога на работу": "demo_work",
        "🚗 практика на утро / дорогу": "demo_work",
        "практика на утро / дорогу": "demo_work",
        "2": "demo_home",
        "2.": "demo_home",
        "2️⃣": "demo_home",
        "2️⃣ вечер / домой": "demo_home",
        "вечер / домой": "demo_home",
        "вечер": "demo_home",
        "дорога домой": "demo_home",
        "🌙 практика на вечер / домой": "demo_home",
        "практика на вечер / домой": "demo_home",
        "weather_city": "weather_city",
        "🏙 изменить город": "weather_city",
        "изменить город": "weather_city",
        "сменить город": "weather_city",
        "город": "weather_city",
        "🔄 обновить погоду": "weather",
        "обновить погоду": "weather",
        "💳 оплатить": "pay",
        "оплатить": "pay",
        "оплата": "pay",
        "pay": "pay",
        "⚙️ настройки": "settings",
        "settings": "settings",
        "repeat": "repeat_audio",
        "/repeat": "repeat_audio",
        "🔁 повторить": "repeat_audio",
        "повторить": "repeat_audio",
        "повторить аудио": "repeat_audio",
        "слушать снова": "repeat_audio",
        "📊 прогресс": "progress",
        "🧾 история": "history",
        "история": "history",
        "history": "history",
        "timeline": "history",
        "/timeline": "history",
        "🔁 другой мессенджер": "switch",
        "другой мессенджер": "switch",
        "switch": "switch",
        "❓ помощь": "help",
        "помощь": "help",
        "help": "help",
        "/help": "help",
    }
    return aliases.get(compact, raw)


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def stable_payload_key(platform: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "ignore")
    return f"{platform}:sha256:" + hashlib.sha256(encoded).hexdigest()


def _payload_text(raw: Any, *, prefer_command: bool = False) -> str:
    """Extract text/command from nested messenger button payloads.

    Native MAX buttons can send both stale display text and a command payload.
    When prefer_command=True, command-like keys are selected before generic
    display text so a button with text='start' and payload.command='score:-4' is
    interpreted as score -4 rather than menu reset.
    """
    if raw in (None, "", b""):
        return ""

    payload: Any = raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return ""
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return value
        if not isinstance(payload, (dict, list)):
            # JSON scalar strings such as "-5", "0" or "1" are valid JSON.
            # They are still user-visible text in MAX webhooks and must not be
            # discarded, otherwise extract_max_message falls back to "start".
            return value

    if isinstance(payload, dict):
        command_keys = ("command", "cmd", "action", "value", "data", "payload", "callback", "button", "body")
        text_keys = ("text", "label")
        keys = command_keys + text_keys if prefer_command else command_keys[:4] + text_keys + command_keys[4:]
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = _payload_text(value, prefer_command=prefer_command)
                if nested:
                    return nested
            if isinstance(value, list):
                for item in value:
                    nested = _payload_text(item, prefer_command=prefer_command)
                    if nested:
                        return nested
    if isinstance(payload, list):
        for item in payload:
            nested = _payload_text(item, prefer_command=prefer_command)
            if nested:
                return nested
    return ""


def text_from_vk_payload(raw: Any) -> str:
    """Extract a command-like text from VK mobile/button payloads."""
    return _payload_text(raw)


def text_from_max_payload(raw: Any) -> str:
    """Extract a command-like text from MAX native button/callback payloads."""
    return _payload_text(raw, prefer_command=True)


def _first_int_from_dict(payload: dict[str, Any], *paths: tuple[str, ...]) -> int | None:
    for path in paths:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(key)
        result = safe_int(current)
        if result is not None:
            return result
    return None


def vk_event_key(payload: dict[str, Any]) -> str:
    obj = payload.get("object") or {}
    message = obj.get("message") or obj
    parts = [
        str(payload.get("event_id") or ""),
        str(message.get("id") or message.get("conversation_message_id") or ""),
        str(message.get("from_id") or message.get("user_id") or ""),
        str(message.get("date") or ""),
    ]
    key = ":".join(part for part in parts if part)
    return key or stable_payload_key("vk", payload)


def max_event_key(payload: dict[str, Any]) -> str:
    message = payload.get("message") or {}
    body = message.get("body") or {}
    callback = payload.get("callback") or payload.get("button") or payload.get("payload") or {}
    if not isinstance(callback, dict):
        callback = {}
    sender = message.get("sender") or payload.get("sender") or callback.get("sender") or {}
    parts = [
        str(payload.get("update_id") or payload.get("event_id") or payload.get("timestamp") or ""),
        str(message.get("message_id") or message.get("id") or body.get("mid") or callback.get("id") or ""),
        str(sender.get("user_id") or sender.get("id") or payload.get("user_id") or payload.get("chat_id") or ""),
        str(message.get("created_at") or payload.get("created_at") or payload.get("timestamp") or ""),
    ]
    key = ":".join(part for part in parts if part)
    return key or stable_payload_key("max", payload)


def extract_vk_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    obj = payload.get("object") or {}
    message = obj.get("message") or obj
    from_id = (
        message.get("from_id")
        or message.get("user_id")
        or message.get("peer_id")
        or obj.get("from_id")
        or obj.get("user_id")
    )
    text = (message.get("text") or obj.get("text") or "").strip()
    if not text:
        text = text_from_vk_payload(message.get("payload") or obj.get("payload") or payload.get("payload"))
    text = normalise_messenger_text(text)
    safe_user_id = safe_int(from_id)
    if safe_user_id is None:
        return None
    return {
        "user_id": safe_user_id,
        "external_user_id": str(from_id),
        "username": None,
        "display_name": None,
        "first_name": None,
        "text": text or "start",
    }


def extract_max_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    message = payload.get("message") or {}
    body = message.get("body") or {}
    callback = payload.get("callback") or payload.get("button") or payload.get("payload") or {}
    if not isinstance(callback, dict):
        callback = {}
    sender = message.get("sender") or payload.get("sender") or callback.get("sender") or {}
    if not isinstance(sender, dict):
        sender = {}

    user_id = _first_int_from_dict(
        {"message": message, "sender": sender, "payload": payload, "callback": callback, "body": body},
        ("message", "sender", "user_id"),
        ("message", "sender", "id"),
        ("sender", "user_id"),
        ("sender", "id"),
        ("callback", "sender", "user_id"),
        ("callback", "sender", "id"),
        ("callback", "user", "user_id"),
        ("callback", "user", "id"),
        ("payload", "user_id"),
        ("payload", "chat_id"),
        ("body", "user_id"),
    )
    if user_id is None:
        return None

    text = (
        text_from_max_payload(body.get("payload"))
        or text_from_max_payload(body.get("button"))
        or text_from_max_payload(body.get("callback"))
        or text_from_max_payload(message.get("payload"))
        or text_from_max_payload(message.get("button"))
        or text_from_max_payload(message.get("callback"))
        or text_from_max_payload(callback)
        or text_from_max_payload(payload.get("payload"))
        or text_from_max_payload(payload.get("button"))
        or text_from_max_payload(payload.get("callback"))
        or text_from_max_payload(payload.get("text"))
        or text_from_max_payload(body.get("text"))
    )
    text = normalise_messenger_text(text or "start")
    full_name = " ".join(part for part in [sender.get("first_name"), sender.get("last_name")] if part).strip() or sender.get("name")
    return {
        "user_id": user_id,
        "external_user_id": str(user_id),
        "username": sender.get("username"),
        "display_name": full_name,
        "first_name": sender.get("first_name") or sender.get("name"),
        "text": text or "start",
    }

# Backward-compatible aliases for the current runtime module during the split wave.
_normalise_messenger_text = normalise_messenger_text
_safe_int = safe_int
_stable_payload_key = stable_payload_key
_text_from_vk_payload = text_from_vk_payload
_text_from_max_payload = text_from_max_payload
_vk_event_key = vk_event_key
_max_event_key = max_event_key
_extract_vk_message = extract_vk_message
_extract_max_message = extract_max_message
