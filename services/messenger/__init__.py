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

from services.messenger.text_ui import MessengerReply, handle_incoming_text
