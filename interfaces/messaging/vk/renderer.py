from __future__ import annotations

"""Render CanonicalResponse into VK keyboard/message kwargs."""

import json
from typing import Any

from interfaces.messaging.contracts import CanonicalButton, CanonicalResponse, RenderedMessage

VK_COLOR_BY_ACTION = {
    "demo": "positive",
    "full": "primary",
    "pay": "primary",
    "progress": "primary",
    "done": "positive",
    "continue": "primary",
}


def _render_button(button: CanonicalButton) -> dict[str, Any]:
    if button.kind == "link":
        if not button.url:
            raise ValueError(f"VK link button {button.text!r} has no URL")
        return {
            "action": {"type": "open_link", "label": button.text, "link": button.url},
            "color": VK_COLOR_BY_ACTION.get(button.action, "secondary"),
        }
    return {
        "action": {
            "type": "text",
            "label": button.text,
            "payload": json.dumps({"command": button.action}, ensure_ascii=False),
        },
        "color": VK_COLOR_BY_ACTION.get(button.action, "secondary"),
    }


def render_vk_response(response: CanonicalResponse) -> RenderedMessage:
    payload: dict[str, Any] = {"message": response.text}
    if response.buttons:
        payload["keyboard_json"] = json.dumps(
            {
                "one_time": False,
                "inline": False,
                "buttons": [[_render_button(button) for button in row] for row in response.buttons],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return RenderedMessage(text=response.text, payload=payload, meta={"platform": "vk"})
