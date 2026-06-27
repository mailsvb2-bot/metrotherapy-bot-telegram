from __future__ import annotations

from typing import Any

_EXPORTS = {
    "MessengerPlatform": ("services.messenger.platforms", "MessengerPlatform"),
    "normalize_platform": ("services.messenger.platforms", "normalize_platform"),
    "get_available_platforms": ("services.messenger.preferences", "get_available_platforms"),
    "get_channel_snapshot": ("services.messenger.preferences", "get_channel_snapshot"),
    "get_preferred_platform": ("services.messenger.preferences", "get_preferred_platform"),
    "record_channel_identity": ("services.messenger.preferences", "record_channel_identity"),
    "record_channel_touch": ("services.messenger.preferences", "record_channel_touch"),
    "resolve_delivery_platform": ("services.messenger.preferences", "resolve_delivery_platform"),
    "set_preferred_platform": ("services.messenger.preferences", "set_preferred_platform"),
    "build_messenger_targets": ("services.messenger.links", "build_messenger_targets"),
    "build_referral_payload": ("services.messenger.links", "build_referral_payload"),
    "EntryActionResult": ("services.messenger.entrypoints", "EntryActionResult"),
    "StartPayload": ("services.messenger.entrypoints", "StartPayload"),
    "parse_start_payload": ("services.messenger.entrypoints", "parse_start_payload"),
    "register_user_entry": ("services.messenger.entrypoints", "register_user_entry"),
    "DeliveryPlan": ("services.messenger.outbound", "DeliveryPlan"),
    "SenderRegistry": ("services.messenger.outbound", "SenderRegistry"),
    "UnsupportedMessengerDelivery": ("services.messenger.outbound", "UnsupportedMessengerDelivery"),
    "build_delivery_plan": ("services.messenger.outbound", "build_delivery_plan"),
    "send_text_to_user": ("services.messenger.outbound", "send_text_to_user"),
    "MessengerReply": ("services.messenger.text_ui", "MessengerReply"),
    "handle_incoming_text": ("services.messenger.text_ui", "handle_incoming_text"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    from importlib import import_module

    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
