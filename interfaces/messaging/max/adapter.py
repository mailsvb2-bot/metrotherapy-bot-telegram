from __future__ import annotations

"""MAX raw-event adapter.

No business logic belongs here. This module only converts raw MAX webhook/LP
updates into the channel-neutral ConversationEvent contract.
"""

from typing import Any

from interfaces.messaging.contracts import ConversationEvent, ConversationUser
from interfaces.messaging.observability import observe
from services.messenger.max_events import extract_max_inbound_message, max_event_key


def adapt_max_event(payload: dict[str, Any]) -> ConversationEvent | None:
    message = extract_max_inbound_message(payload)
    if message is None:
        observe("max", "inbound", "rejected", reason="unsupported_payload")
        return None

    raw_text = (message.text or "").strip() or "start"
    kind = "button" if any(key in payload for key in ("callback", "button")) else "message"
    if raw_text == "start":
        kind = "start"

    user = ConversationUser(
        user_id=message.user_id,
        external_user_id=message.external_user_id,
        platform="max",
        username=message.username,
        display_name=message.display_name,
        first_name=message.first_name,
    )
    event = ConversationEvent(
        platform="max",
        kind=kind,
        user=user,
        text=raw_text,
        event_key=max_event_key(payload),
        raw=payload,
        meta={"source": "max"},
    )
    observe("max", "inbound", "ok", kind=kind, has_text=bool(raw_text))
    return event
