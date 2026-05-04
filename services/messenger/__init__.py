from __future__ import annotations

from services.messenger.platforms import MessengerPlatform, normalize_platform
from services.messenger.preferences import (
    get_available_platforms,
    get_channel_snapshot,
    get_preferred_platform,
    record_channel_identity,
    record_channel_touch,
    resolve_delivery_platform,
    set_preferred_platform,
)
from services.messenger.links import build_messenger_targets, build_referral_payload
from services.messenger.entrypoints import EntryActionResult, StartPayload, parse_start_payload, register_user_entry
from services.messenger.outbound import DeliveryPlan, SenderRegistry, UnsupportedMessengerDelivery, build_delivery_plan, send_text_to_user


__all__ = [
    "MessengerPlatform",
    "normalize_platform",
    "get_available_platforms",
    "get_channel_snapshot",
    "get_preferred_platform",
    "record_channel_identity",
    "record_channel_touch",
    "resolve_delivery_platform",
    "set_preferred_platform",
    "build_messenger_targets",
    "build_referral_payload",
    "EntryActionResult",
    "StartPayload",
    "parse_start_payload",
    "register_user_entry",
    "DeliveryPlan",
    "SenderRegistry",
    "UnsupportedMessengerDelivery",
    "build_delivery_plan",
    "send_text_to_user",
    "MessengerReply",
    "handle_incoming_text",
]


def __getattr__(name: str):
    """Keep legacy package imports without creating a text-ui import cycle.

    Importing services.mood_text_flow imports submodules under services.messenger.
    Python executes this package __init__ first. If __init__ eagerly imports
    services.messenger.text_ui, text_ui imports mood_text_flow back and the app
    crashes with a partially initialized module. Keep text_ui lazy.
    """
    if name in {"MessengerReply", "handle_incoming_text"}:
        from services.messenger.text_ui import MessengerReply, handle_incoming_text

        return {"MessengerReply": MessengerReply, "handle_incoming_text": handle_incoming_text}[name]
    raise AttributeError(name)
