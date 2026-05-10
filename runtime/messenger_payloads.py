from __future__ import annotations

import hashlib
import json
from typing import Any


def normalise_messenger_text(text: str) -> str:
    """Normalize human/mobile button labels to canonical text commands."""
    raw = (text or "").strip()
    compact = raw.casefold().replace("ё", "е")
    compact = " ".join(compact.split())

    aliases = {
        "/start": "start",
        "start": "start",
        "старт": "start",
        "начать": "start",
        "🌿 начать": "start",
        "меню": "start",
        "главное меню": "start",
        "⬅️ назад": "start",
        "назад": "start",
        "⬅️ меню": "start",
        "🌿 попробовать бесплатно": "demo",
        "попробовать бесплатно": "demo",
        "бесплатная практика": "demo",
        "демо": "demo",
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
        "🔐 полный маршрут": "full",
        "полный маршрут": "full",
        "полный доступ": "full",
        "💳 тарифы": "pay",
        "тарифы": "pay",
        "📈 мой прогресс": "progress",
        "мой прогресс": "progress",
        "анализ": "progress",
        "анализ моего состояния": "progress",
        "🧠 настройки": "settings",
        "📣 посоветовать": "share",
        "посоветовать": "share",
        "🌤 погода": "weather",
        "погода": "weather",
        "🔄 обновить погоду": "weather",
        "обновить погоду": "weather",
        "weather_city": "weather_city",
        "🏙 изменить город": "weather_city",
        "изменить город": "weather_city",
        "сменить город": "weather_city",
        "город": "weather_city",
        "🎧 получить аудио": "continue",
        "получить аудио": "continue",
        "продолжить": "continue",
        "continue": "continue",
        "✅ прослушал": "done",
        "прослушал": "done",
        "готово": "done",
        "done": "done",
        "🔁 повторить": "repeat_audio",
        "повторить": "repeat_audio",
        "повторить аудио": "repeat_audio",
        "слушать снова": "repeat_audio",
        "💳 оплатить": "pay",
        "оплатить": "pay",
        "оплата": "pay",
        "pay": "pay",
        "🎁 подарить": "gift",
        "подарить": "gift",
        "подарок": "gift",
        "gift": "gift",
        "↗️ поделиться": "share",
        "поделиться": "share",
        "share": "share",
        "⚙️ настройки": "settings",
        "настройки": "settings",
        "settings": "settings",
        "📊 прогресс": "progress",
        "прогресс": "progress",
        "progress": "progress",
        "🧾 история": "history",
        "история": "history",
        "history": "history",
        "🔁 другой мессенджер": "switch",
        "другой мессенджер": "switch",
        "switch": "switch",
        "❓ помощь": "help",
        "помощь": "help",
        "help": "help",
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


def text_from_vk_payload(raw: Any) -> str:
    """Extract a command-like text from VK mobile/button payloads."""
    if raw in (None, "", b""):
        return ""

    payload: Any = raw
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return ""
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return value

    if isinstance(payload, dict):
        for key in ("command", "cmd", "action", "button", "value", "text", "payload"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = text_from_vk_payload(value)
                if nested:
                    return nested
    return ""


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
    parts = [
        str(payload.get("update_id") or payload.get("event_id") or ""),
        str(message.get("message_id") or message.get("id") or body.get("mid") or ""),
        str((message.get("sender") or {}).get("user_id") or (message.get("sender") or {}).get("id") or ""),
        str(message.get("created_at") or payload.get("timestamp") or ""),
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
    sender = message.get("sender") or {}
    body = message.get("body") or {}
    user_id = sender.get("user_id") or sender.get("id")
    safe_user_id = safe_int(user_id)
    if safe_user_id is None:
        return None
    text = (body.get("text") or "").strip()
    full_name = " ".join(part for part in [sender.get("first_name"), sender.get("last_name")] if part).strip() or sender.get("name")
    return {
        "user_id": safe_user_id,
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
_vk_event_key = vk_event_key
_max_event_key = max_event_key
_extract_vk_message = extract_vk_message
_extract_max_message = extract_max_message
