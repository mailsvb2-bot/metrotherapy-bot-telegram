from __future__ import annotations

"""Canonical adapter for inbound MAX webhook/LP events.

The rest of the project should not depend on raw MAX payload shapes. This file
normalizes MAX updates into the same text-command envelope consumed by
services.messenger.text_ui. It intentionally contains no business logic: MAX is
only a transport/channel, not a second decision path.
"""

from dataclasses import dataclass
from typing import Any
import hashlib
import json


@dataclass(frozen=True)
class MaxInboundMessage:
    user_id: int
    external_user_id: str
    text: str
    username: str | None = None
    display_name: str | None = None
    first_name: str | None = None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalise_max_text(text: str) -> str:
    raw = (text or "").strip()
    compact = raw.casefold().replace("ё", "е")
    compact = " ".join(compact.split())
    aliases = {
        "/start": "start",
        "start": "start",
        "старт": "start",
        "начать": "start",
        "начало": "start",
        "меню": "start",
        "главное меню": "start",
        "go": "start",
        "run": "start",
        "продолжить": "continue",
        "получить аудио": "continue",
        "🎧 получить аудио": "continue",
        "done": "done",
        "готово": "done",
        "прослушал": "done",
        "✅ прослушал": "done",
    }
    return aliases.get(compact, raw)


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _extract_text_candidate(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(decoded, dict):
            nested = _extract_text_from_payload(decoded)
            return nested or raw
        return raw
    if isinstance(value, dict):
        return _extract_text_from_payload(value)
    return str(value).strip()


def _extract_text_from_payload(payload: dict[str, Any]) -> str:
    """Extract a command/text from MAX message/callback payloads.

    MAX button updates may preserve the button payload, send only the button text,
    or wrap data under callback/body/message. We prefer explicit command-like
    fields, then text-like fields, then recursively nested payload objects.
    """
    for key in ("command", "cmd", "action", "value", "payload", "button_payload"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _extract_text_candidate(value)
        if isinstance(value, dict):
            nested = _extract_text_from_payload(value)
            if nested:
                return nested

    for key in ("text", "message", "title", "label"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = _extract_text_from_payload(value)
            if nested:
                return nested

    return ""


def _sender_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    message = _first_dict(payload.get("message"), payload.get("event"), payload.get("callback"), payload.get("body"))
    callback = _first_dict(payload.get("callback"), message.get("callback"), message.get("payload"), payload.get("button"), message.get("button"))
    chat = _first_dict(payload.get("chat"), message.get("chat"), callback.get("chat"))
    user = _first_dict(payload.get("user"), message.get("user"), callback.get("user"))
    sender = _first_dict(
        payload.get("sender"),
        payload.get("from"),
        payload.get("recipient"),
        message.get("sender"),
        message.get("from"),
        message.get("recipient"),
        callback.get("sender"),
        callback.get("from"),
        callback.get("recipient"),
        user,
        chat,
    )
    if sender:
        return sender

    # Some MAX callback/bot_started payloads expose only flat ids.
    for key in ("user_id", "sender_id", "chat_id", "recipient_id"):
        if payload.get(key) is not None:
            return {"user_id": payload.get(key)}
    for src in (message, callback, chat, user):
        for key in ("user_id", "sender_id", "chat_id", "recipient_id", "id"):
            if src.get(key) is not None:
                return {"user_id": src.get(key)}
    return {}


def _text_from_max_payload(payload: dict[str, Any]) -> str:
    message = _first_dict(payload.get("message"), payload.get("event"), payload.get("body"))
    body = _first_dict(message.get("body"), payload.get("body"))
    callback = _first_dict(payload.get("callback"), message.get("callback"), payload.get("button"), message.get("button"))

    for candidate in (
        callback,
        callback.get("payload") if isinstance(callback, dict) else None,
        message.get("payload"),
        body.get("payload"),
        body,
        message,
        payload,
    ):
        text = _extract_text_candidate(candidate)
        if text:
            return text
    return ""


def extract_max_inbound_message(payload: dict[str, Any]) -> MaxInboundMessage | None:
    sender = _sender_from_payload(payload)
    user_id = (
        sender.get("user_id")
        or sender.get("id")
        or payload.get("user_id")
        or payload.get("sender_id")
    )
    safe_user_id = _safe_int(user_id)
    if safe_user_id is None:
        return None

    text = _text_from_max_payload(payload).strip() or "start"
    full_name = " ".join(
        part for part in [sender.get("first_name"), sender.get("last_name")] if part
    ).strip() or sender.get("name")

    return MaxInboundMessage(
        user_id=safe_user_id,
        external_user_id=str(user_id),
        username=sender.get("username"),
        display_name=full_name,
        first_name=sender.get("first_name") or sender.get("name"),
        text=text,
    )


def max_event_key(payload: dict[str, Any]) -> str:
    message = _first_dict(payload.get("message"), payload.get("event"), payload.get("body"))
    body = _first_dict(message.get("body"), payload.get("body"))
    callback = _first_dict(payload.get("callback"), message.get("callback"), payload.get("button"), message.get("button"))
    sender = _sender_from_payload(payload)
    parts = [
        str(payload.get("update_id") or payload.get("event_id") or ""),
        str(message.get("message_id") or message.get("id") or body.get("mid") or callback.get("callback_id") or callback.get("id") or ""),
        str(sender.get("user_id") or sender.get("id") or payload.get("user_id") or ""),
        str(message.get("created_at") or payload.get("timestamp") or callback.get("timestamp") or ""),
    ]
    key = ":".join(part for part in parts if part)
    if key:
        return key
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "ignore")
    return "max:sha256:" + hashlib.sha256(encoded).hexdigest()

# --- compatible MAX extractor override v2026-05-04 ---
# Keeps existing tests/contract intact while adding bot_started and flat callback support.

def _compat_json_loads(value):
    import json
    try:
        return json.loads(value)
    except Exception:
        return None


def _compat_sha256_payload(payload):
    import hashlib
    import json
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _compat_walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _compat_walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _compat_walk_dicts(child)


def _compat_safe_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _compat_first_dict(*values):
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _compat_extract_sender(payload):
    # Prefer explicit user-like containers, then every dict as fallback.
    preferred = []

    for obj in _compat_walk_dicts(payload):
        for key in ("user", "sender", "from", "recipient", "chat", "author"):
            value = obj.get(key)
            if isinstance(value, dict):
                preferred.append(value)

    for obj in _compat_walk_dicts(payload):
        preferred.append(obj)

    for obj in preferred:
        for key in ("user_id", "sender_id", "chat_id", "recipient_id", "id"):
            safe = _compat_safe_int(obj.get(key))
            if safe is not None:
                return obj, obj.get(key)

    return {}, None


def _compat_command_from_json_payload(value):
    if not isinstance(value, str):
        return None
    decoded = _compat_json_loads(value.strip())
    if not isinstance(decoded, dict):
        return None
    for key in ("command", "cmd", "action", "payload", "text"):
        inner = decoded.get(key)
        if isinstance(inner, str) and inner.strip():
            return inner.strip()
    return None


def _compat_extract_text(payload):
    update_type = str(payload.get("update_type") or payload.get("type") or "").strip()

    # bot_started often arrives without message text. For the product UX this is a start.
    if update_type == "bot_started":
        return "start"

    callback = _compat_first_dict(
        payload.get("callback"),
        payload.get("button"),
        payload.get("message_callback"),
    )

    if callback:
        # Preserve old contract: JSON payload {"command": "..."} becomes command.
        payload_value = callback.get("payload")
        json_command = _compat_command_from_json_payload(payload_value)
        if json_command:
            return json_command

        # For explicit start aliases only, map to canonical start.
        for key in ("payload", "command", "data"):
            value = callback.get(key)
            if isinstance(value, str) and value.strip():
                low = value.strip().casefold().replace("ё", "е")
                if low in {"/start", "start", "старт", "начать", "меню", "главное меню"}:
                    return "start"
                return value.strip()

        # Preserve old contract: button text fallback remains raw, e.g. "✅ Прослушал".
        for key in ("text", "label", "title"):
            value = callback.get(key)
            if isinstance(value, str) and value.strip():
                low = value.strip().casefold().replace("ё", "е")
                if low in {"/start", "start", "старт", "начать", "меню", "главное меню"}:
                    return "start"
                return value.strip()

    # General message text extraction. Preserve raw text where possible.
    for obj in _compat_walk_dicts(payload):
        for key in ("text",):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                low = value.strip().casefold().replace("ё", "е")
                if low in {"/start", "start", "старт", "начать", "меню", "главное меню"}:
                    return "start"
                return value.strip()

    for obj in _compat_walk_dicts(payload):
        for key in ("payload", "command", "data", "label", "title"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                low = value.strip().casefold().replace("ё", "е")
                if low in {"/start", "start", "старт", "начать", "меню", "главное меню"}:
                    return "start"
                return value.strip()

    return "start"


def extract_max_inbound_message(payload):
    sender, raw_user_id = _compat_extract_sender(payload)
    safe_user_id = _compat_safe_int(raw_user_id)
    if safe_user_id is None:
        return None

    full_name = " ".join(
        str(part).strip()
        for part in (sender.get("first_name"), sender.get("last_name"))
        if part
    ).strip() or sender.get("name")

    return MaxInboundMessage(
        user_id=safe_user_id,
        external_user_id=str(raw_user_id),
        text=_compat_extract_text(payload),
        username=sender.get("username"),
        display_name=full_name,
        first_name=sender.get("first_name") or sender.get("name"),
    )


def max_event_key(payload):
    message_obj = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    callback_obj = payload.get("callback") if isinstance(payload.get("callback"), dict) else {}

    update_id = payload.get("update_id") or payload.get("event_id")
    message_id = message_obj.get("id") or callback_obj.get("id")

    sender_obj = {}
    for candidate in (
        message_obj.get("sender"),
        message_obj.get("recipient"),
        callback_obj.get("user"),
        callback_obj.get("sender"),
        payload.get("user"),
        payload.get("sender"),
    ):
        if isinstance(candidate, dict):
            sender_obj = candidate
            break

    sender_id = sender_obj.get("user_id") or payload.get("chat_id") or payload.get("user_id")
    created_at = message_obj.get("created_at") or payload.get("timestamp")

    # Preserve existing tested shape:
    # update_id + message.id + sender + created_at => u1:m1:42:123
    # update_id + message.id + sender            => u1:m1:42
    if update_id and message_id and sender_id and created_at:
        return f"{update_id}:{message_id}:{sender_id}:{created_at}"
    if update_id and message_id and sender_id:
        return f"{update_id}:{message_id}:{sender_id}"

    # Callback/bot_started stable fallback.
    if update_id and sender_id:
        return f"{update_id}:{sender_id}"
    if update_id:
        return str(update_id)
    if payload.get("event_id") and sender_id:
        return f"{payload.get('event_id')}:{sender_id}"

    return "max:sha256:" + _compat_sha256_payload(payload)
