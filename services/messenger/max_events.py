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
    callback = _first_dict(payload.get("callback"), message.get("callback"), message.get("payload"))
    return _first_dict(
        payload.get("sender"),
        payload.get("user"),
        payload.get("from"),
        message.get("sender"),
        message.get("user"),
        message.get("from"),
        callback.get("sender"),
        callback.get("user"),
        callback.get("from"),
    )


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
