from __future__ import annotations

"""Deliver CanonicalResponse through a Telegram sender boundary.

The sender is intentionally duck-typed. In runtime it can be an aiogram Bot,
or a small adapter exposing send_message(chat_id, text, **kwargs). This keeps
Unified Messaging free from direct aiogram imports.
"""

from typing import Any

from interfaces.messaging.contracts import CanonicalResponse
from interfaces.messaging.telegram.renderer import render_telegram_response


async def send_canonical_telegram_response(sender: Any, chat_id: int | str, response: CanonicalResponse) -> Any:
    rendered = render_telegram_response(response)
    kwargs: dict[str, Any] = {}
    reply_markup = rendered.payload.get("reply_markup")
    if reply_markup:
        kwargs["reply_markup"] = reply_markup
    return await sender.send_message(chat_id, rendered.text, **kwargs)
