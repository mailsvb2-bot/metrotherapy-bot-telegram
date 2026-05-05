from __future__ import annotations

"""Deliver CanonicalResponse through the current MAX sender.

This is the safe bridge between the new unified renderer and the existing async
runtime sender. It keeps MAX-specific rendering outside business logic while
avoiding a risky full rewrite of runtime/messenger_senders.py.
"""

from typing import Any

from interfaces.messaging.contracts import CanonicalResponse
from interfaces.messaging.max.renderer import render_max_response
from interfaces.messaging.observability import observe


def _first_keyboard_attachment(payload: dict[str, Any]) -> dict[str, Any] | None:
    attachments = payload.get("attachments") or []
    if not isinstance(attachments, list):
        return None
    for attachment in attachments:
        if isinstance(attachment, dict) and attachment.get("type") == "inline_keyboard":
            return attachment
    return None


def _post_score_keyboard() -> dict[str, Any]:
    return {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [
                [
                    {
                        "type": "message",
                        "text": "📈 Посмотреть график изменения состояния",
                        "payload": "progress",
                    }
                ],
                [
                    {"type": "message", "text": "🎧 Другая практика", "payload": "demo"},
                    {"type": "message", "text": "🔐 Открыть полный маршрут", "payload": "full"},
                ],
                [{"type": "message", "text": "🏠 Меню", "payload": "start"}],
            ]
        },
    }


def _is_post_score_response(text: str) -> bool:
    return (text or "").lstrip().startswith("✅ Оценку после прослушивания")


async def send_canonical_max_response(sender: Any, external_user_id: str, response: CanonicalResponse) -> Any:
    rendered = render_max_response(response)
    keyboard = _first_keyboard_attachment(rendered.payload)
    if keyboard is None and _is_post_score_response(rendered.text):
        keyboard = _post_score_keyboard()
    try:
        result = await sender.send_text(
            external_user_id,
            rendered.text,
            max_keyboard=keyboard,
        )
    except Exception as exc:
        observe(
            "max",
            "delivery",
            "error",
            has_buttons=bool(keyboard),
            error_type=type(exc).__name__,
        )
        raise
    observe("max", "delivery", "ok", has_buttons=bool(keyboard))
    return result
