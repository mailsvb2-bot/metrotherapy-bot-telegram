from __future__ import annotations

"""Render CanonicalResponse into Telegram Bot API-compatible payload.

This renderer intentionally returns plain dictionaries instead of importing
aiogram types. That keeps the unified interface layer lightweight and testable;
runtime handlers can convert the payload to aiogram objects at the boundary.
"""

from typing import Any

from interfaces.messaging.contracts import CanonicalButton, CanonicalResponse, RenderedMessage


def _render_button(button: CanonicalButton) -> dict[str, Any]:
    if button.kind == "link":
        if not button.url:
            raise ValueError(f"Telegram link button {button.text!r} has no URL")
        return {"text": button.text, "url": button.url}
    return {"text": button.text, "callback_data": button.action}


def render_telegram_response(response: CanonicalResponse) -> RenderedMessage:
    payload: dict[str, Any] = {"text": response.text}
    if response.buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [_render_button(button) for button in row]
                for row in response.buttons
            ]
        }
    return RenderedMessage(text=response.text, payload=payload, meta={"platform": "telegram"})
