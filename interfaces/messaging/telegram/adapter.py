from __future__ import annotations

"""Telegram raw-update adapter for the Unified Conversation Layer.

This adapter accepts Telegram Bot API update dictionaries. It does not import
aiogram and does not contain business logic.
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


def telegram_event_key(update: dict[str, Any]) -> str:
    update_id = str(update.get("update_id") or "")
    message = update.get("message") or update.get("callback_query") or update.get("edited_message") or {}
    message_id = ""
    user_id = ""
    if isinstance(message, dict):
        if "message" in message and isinstance(message.get("message"), dict):
            nested_message = message.get("message") or {}
            message_id = str(nested_message.get("message_id") or "")
        else:
            message_id = str(message.get("message_id") or message.get("id") or "")
        sender = message.get("from") or {}
        if isinstance(sender, dict):
            user_id = str(sender.get("id") or "")
    key = ":".join(part for part in [update_id, message_id, user_id] if part)
    if key:
        return key
    encoded = json.dumps(update, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "ignore")
    return "telegram:sha256:" + hashlib.sha256(encoded).hexdigest()


def _user_from_sender(sender: dict[str, Any]) -> ConversationUser | None:
    safe_user_id = _safe_int(sender.get("id"))
    if safe_user_id is None:
        return None
    full_name = " ".join(
        part for part in [sender.get("first_name"), sender.get("last_name")] if part
    ).strip() or None
    return ConversationUser(
        user_id=safe_user_id,
        external_user_id=str(sender.get("id")),
        platform="telegram",
        username=sender.get("username"),
        display_name=full_name,
        first_name=sender.get("first_name"),
    )


def adapt_telegram_update(update: dict[str, Any]) -> ConversationEvent | None:
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        sender = callback.get("from") or {}
        if not isinstance(sender, dict):
            return None
        user = _user_from_sender(sender)
        if user is None:
            return None
        text = str(callback.get("data") or "start").strip() or "start"
        return ConversationEvent(
            platform="telegram",
            kind="button",
            user=user,
            text=text,
            event_key=telegram_event_key(update),
            raw=update,
            meta={"source": "telegram", "callback_query_id": callback.get("id")},
        )

    message = update.get("message") or update.get("edited_message")
    if isinstance(message, dict):
        sender = message.get("from") or {}
        if not isinstance(sender, dict):
            return None
        user = _user_from_sender(sender)
        if user is None:
            return None
        text = str(message.get("text") or "start").strip() or "start"
        return ConversationEvent(
            platform="telegram",
            kind="start" if text == "/start" or text == "start" else "message",
            user=user,
            text=text,
            event_key=telegram_event_key(update),
            raw=update,
            meta={"source": "telegram"},
        )

    return None
