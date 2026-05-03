from __future__ import annotations

"""Deliver CanonicalResponse through the current VK sender."""

from typing import Any

from interfaces.messaging.contracts import CanonicalResponse
from interfaces.messaging.vk.renderer import render_vk_response


async def send_canonical_vk_response(sender: Any, external_user_id: str, response: CanonicalResponse) -> Any:
    rendered = render_vk_response(response)
    kwargs = {}
    if rendered.payload.get("keyboard_json"):
        kwargs["keyboard_json"] = rendered.payload["keyboard_json"]
    return await sender.send_text(external_user_id, rendered.text, **kwargs)
