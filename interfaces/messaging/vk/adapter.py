from __future__ import annotations

"""VK raw-event adapter.

No business logic belongs here. This module only converts raw VK webhook
updates into the channel-neutral ConversationEvent contract.
"""

from typing import Any
import hashlib
import json

from interfaces.messaging.contracts import ConversationEvent, ConversationUser


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text_from_vk_payload(raw: Any) -> str:
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
                return _text_from_vk_payload(value) if value.strip().startswith("{") else value.strip()
            if isinstance(value, dict):
                nested = _text_from_vk_payload(value)
                if nested:
                    return nested
    return ""


def _normalise_vk_text(text: str) -> str:
    raw = (text or "").strip()
    compact = raw.casefold().replace("ё", "е")
    compact = " ".join(compact.split())
    aliases = {
        "/start": "start",
        "start": "start",
        "старт": "start",
        "начать": "start",
        "меню": "start",
        "главное меню": "start",
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
        "⬅️ назад": "start",
        "назад": "start",
        "🔐 полный маршрут": "full",
        "полный маршрут": "full",
        "полный доступ": "full",
        "💳 тарифы": "pay",
        "тарифы": "pay",
        "🎁 подарить": "gift",
        "подарить": "gift",
        "📈 мой прогресс": "progress",
        "📊 прогресс": "progress",
        "прогресс": "progress",
        "🧠 настройки": "settings",
        "⚙️ настройки": "settings",
        "настройки": "settings",
        "📣 посоветовать": "share",
        "посоветовать": "share",
        "↗️ поделиться": "share",
        "поделиться": "share",
        "🌤 погода": "weather",
        "погода": "weather",
        "🎧 получить аудио": "continue",
        "получить аудио": "continue",
        "продолжить": "continue",
        "✅ прослушал": "done",
        "прослушал": "done",
        "готово": "done",
        "🔁 повторить аудио": "repeat_audio",
        "повторить аудио": "repeat_audio",
    }
    return aliases.get(compact, raw)


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
    if key:
        return key
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "ignore")
    return "vk:sha256:" + hashlib.sha256(encoded).hexdigest()


def adapt_vk_event(payload: dict[str, Any]) -> ConversationEvent | None:
    obj = payload.get("object") or {}
    message = obj.get("message") or obj
    from_id = (
        message.get("from_id")
        or message.get("user_id")
        or message.get("peer_id")
        or obj.get("from_id")
        or obj.get("user_id")
    )
    safe_user_id = _safe_int(from_id)
    if safe_user_id is None:
        return None

    text = (message.get("text") or obj.get("text") or "").strip()
    if not text:
        text = _text_from_vk_payload(message.get("payload") or obj.get("payload") or payload.get("payload"))
    text = _normalise_vk_text(text) or "start"

    user = ConversationUser(
        user_id=safe_user_id,
        external_user_id=str(from_id),
        platform="vk",
    )
    return ConversationEvent(
        platform="vk",
        kind="button" if message.get("payload") or obj.get("payload") or payload.get("payload") else "message",
        user=user,
        text=text,
        event_key=vk_event_key(payload),
        raw=payload,
        meta={"source": "vk"},
    )
