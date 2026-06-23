from __future__ import annotations

from typing import Any, Dict, Optional
from aiogram import Bot
from aiogram.types import Message, CallbackQuery


def _callback_message(cb: CallbackQuery) -> Message | None:
    message = cb.message
    return message if isinstance(message, Message) else None

class TelegramActionRunner:
    def __init__(self, bot: Bot, message: Optional[Message] = None, cb: Optional[CallbackQuery] = None):
        self.bot = bot
        self.message = message
        self.cb = cb

    async def run(self, payload: Dict[str, Any]) -> Any:
        t = str(payload.get("type") or "noop")
        if t == "safe_content":
            txt = "⚠️ Временно недоступно.\n\nПожалуйста, попробуйте позже."
            if self.message:
                return await self.message.answer(txt)
            if self.cb:
                callback_message = _callback_message(self.cb)
                if callback_message is not None:
                    return await callback_message.answer(txt)
            return None
        if t == "send_text":
            text = str(payload.get("text") or "")
            if self.message:
                return await self.message.answer(text)
            if self.cb:
                callback_message = _callback_message(self.cb)
                if callback_message is not None:
                    return await callback_message.answer(text)
            return None
        return None
