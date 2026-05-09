from __future__ import annotations

import json
import logging

from aiohttp import web

from config.settings import settings
from runtime.messenger_senders import MessengerTransportError
from runtime.messenger_payloads import (
    extract_max_message,
    extract_vk_message,
    max_event_key,
    normalise_messenger_text,
    vk_event_key,
)
from services.events import log_event
from services.messenger.reply_dispatcher import send_reply_bundle
from services.messenger.text_ui import handle_incoming_text
from services.messenger.webhook_dedupe import register_inbound_event

log = logging.getLogger(__name__)


async def vk_webhook(request: web.Request) -> web.Response:
    body = await request.text()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400, text="invalid json")
    secret = (settings.VK_SECRET or "").strip()
    if secret and payload.get("secret") not in {"", None, secret}:
        return web.Response(status=403, text="forbidden")

    event_type = (payload.get("type") or "").strip()
    if event_type == "confirmation":
        return web.Response(text=(settings.VK_CONFIRMATION_TOKEN or "").strip())
    if event_type != "message_new":
        return web.Response(text="ok")
    if not register_inbound_event("vk", vk_event_key(payload), payload):
        return web.Response(text="ok")

    extracted = extract_vk_message(payload)
    if not extracted:
        return web.Response(text="ok")

    canonical_user_id, replies = handle_incoming_text(
        extracted["user_id"],
        platform="vk",
        external_user_id=extracted["external_user_id"],
        text=normalise_messenger_text(extracted["text"]),
        username=extracted["username"],
        display_name=extracted["display_name"],
        first_name=extracted["first_name"],
    )

    log.info(
        "VK message_new processed: external_user_id=%s canonical_user_id=%s text=%r replies=%s",
        extracted["external_user_id"],
        canonical_user_id,
        extracted["text"][:120],
        len(replies),
    )

    try:
        await send_reply_bundle("vk", extracted["external_user_id"], canonical_user_id, replies)
        log.info(
            "VK replies sent: external_user_id=%s canonical_user_id=%s replies=%s",
            extracted["external_user_id"],
            canonical_user_id,
            len(replies),
        )
    except MessengerTransportError:
        log.exception("VK send failed")
        log_event(canonical_user_id, "vk_send_failed", {})
    log_event(canonical_user_id, "vk_webhook_inbound", {"text": extracted["text"][:120], "replies": len(replies)})
    return web.Response(text="ok")


async def max_webhook(request: web.Request) -> web.Response:
    body = await request.text()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400, text="invalid json")
    update_type = (payload.get("update_type") or "").strip()
    if update_type and update_type != "message_created":
        return web.json_response({"ok": True})

    if not register_inbound_event("max", max_event_key(payload), payload):
        return web.json_response({"ok": True})

    extracted = extract_max_message(payload)
    if not extracted:
        return web.json_response({"ok": True})

    canonical_user_id, replies = handle_incoming_text(
        extracted["user_id"],
        platform="max",
        external_user_id=extracted["external_user_id"],
        text=normalise_messenger_text(extracted["text"]),
        username=extracted["username"],
        display_name=extracted["display_name"],
        first_name=extracted["first_name"],
    )
    try:
        await send_reply_bundle("max", extracted["external_user_id"], canonical_user_id, replies)
    except MessengerTransportError:
        log.exception("MAX send failed")
        log_event(canonical_user_id, "max_send_failed", {})
    log_event(canonical_user_id, "max_webhook_inbound", {"text": extracted["text"][:120]})
    return web.json_response({"ok": True})
