from __future__ import annotations

"""Render channel-neutral CanonicalResponse into MAX message payloads."""

from typing import Any

from interfaces.messaging.contracts import CanonicalButton, CanonicalResponse, RenderedMessage


def _render_button(button: CanonicalButton) -> dict[str, Any]:
    if button.kind == "link":
        if not button.url:
            raise ValueError(f"MAX link button {button.text!r} has no URL")
        return {"type": "link", "text": button.text, "url": button.url}
    return {"type": "message", "text": button.text, "payload": button.action}


def render_max_response(response: CanonicalResponse) -> RenderedMessage:
    payload: dict[str, Any] = {"text": response.text}
    if response.buttons:
        payload["attachments"] = [
            {
                "type": "inline_keyboard",
                "payload": {
                    "buttons": [
                        [_render_button(button) for button in row]
                        for row in response.buttons
                    ]
                },
            }
        ]
    return RenderedMessage(text=response.text, payload=payload, meta={"platform": "max"})
