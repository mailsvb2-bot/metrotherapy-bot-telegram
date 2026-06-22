from __future__ import annotations

from typing import Any, Dict
from aiogram.types import Update

def build_world_state(update: Update, intent: str = "") -> Dict[str, Any]:
    user_id = None
    chat_id = None
    if update.message and update.message.from_user:
        user_id = update.message.from_user.id
        chat_id = update.message.chat.id if update.message.chat else None
    if update.callback_query and update.callback_query.from_user:
        user_id = update.callback_query.from_user.id
        chat_id = update.callback_query.message.chat.id if update.callback_query.message and update.callback_query.message.chat else None
    return {
        "intent": intent,
        "user_id": user_id,
        "chat_id": chat_id,
        "update_type": "callback" if update.callback_query else "message",
        "payload": update.model_dump() if hasattr(update, "model_dump") else {},
    }
