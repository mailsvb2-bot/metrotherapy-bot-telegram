from .platforms import MessengerPlatform, normalize_platform
from .preferences import (
    get_available_platforms,
    get_channel_snapshot,
    get_preferred_platform,
    record_channel_identity,
    record_channel_touch,
    resolve_delivery_platform,
    set_preferred_platform,
)
from .links import build_messenger_targets, build_referral_payload
from .entrypoints import EntryActionResult, StartPayload, parse_start_payload, register_user_entry
from .outbound import DeliveryPlan, SenderRegistry, UnsupportedMessengerDelivery, build_delivery_plan, send_text_to_user

from .text_ui import MessengerReply, handle_incoming_text
