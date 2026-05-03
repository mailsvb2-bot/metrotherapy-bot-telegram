from __future__ import annotations

"""Bridge ConversationEvent into the existing Metrotherapy conversation flow.

This is the central migration seam for the Unified Conversation Layer:

  ConversationEvent -> existing text_ui/funnel -> MessengerReply -> CanonicalResponse

It intentionally delegates business decisions to services.messenger.text_ui and
contains no separate funnel logic.
"""

from interfaces.messaging.contracts import ConversationEvent, CanonicalResponse
from interfaces.messaging.legacy_bridge import messenger_replies_to_canonical
from services.messenger.text_ui import handle_incoming_text


def handle_conversation_event(event: ConversationEvent) -> tuple[int, list[CanonicalResponse]]:
    """Handle a normalized conversation event through the canonical legacy flow.

    Returns the canonical user id resolved by the existing identity bridge plus
    channel-neutral responses ready for platform renderers.
    """
    canonical_user_id, replies = handle_incoming_text(
        int(event.user.user_id),
        platform=event.platform,
        external_user_id=event.user.external_user_id,
        text=event.text,
        username=event.user.username,
        display_name=event.user.display_name,
        first_name=event.user.first_name,
    )
    return int(canonical_user_id), messenger_replies_to_canonical(replies)
