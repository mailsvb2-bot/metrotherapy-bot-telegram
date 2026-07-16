from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from typing import Any

from aiohttp import web

from config.settings import settings
from runtime.messenger_payloads import (
    extract_max_message,
    extract_vk_message,
    max_event_key,
    normalise_messenger_text,
    text_from_max_payload,
    text_from_vk_payload,
    vk_event_key,
)
from runtime.messenger_senders import MessengerTransportError, VkBotSender
from services.events import log_event
from services.gift_claims import claim_gift_token, is_gift_token, normalize_gift_token
from services.messenger.delivery_outbox import persist_reply_bundle
from services.messenger.entrypoints import register_user_entry
from services.messenger.observability import log_payload_normalized
from services.messenger.text_ui_router import MessengerReply, handle_incoming_text
from services.messenger.webhook_dedupe import claim_inbound_event, fail_inbound_event

log = logging.getLogger(__name__)

VK_PROCESSABLE_EVENT_TYPES = {"message_new", "message_event"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _app_env() -> str:
    return (os.getenv("APP_ENV") or getattr(settings, "APP_ENV", "") or "dev").strip().lower()


def _allow_insecure_messenger_webhooks() -> bool:
    if _app_env() in {"prod", "production", "stage", "staging"}:
        return False
    return _env_bool("ALLOW_INSECURE_MESSENGER_WEBHOOKS", False)


def _provided_max_secret(request: web.Request, payload: dict) -> str:
    return (
        request.headers.get("X-Max-Webhook-Secret")
        or request.headers.get("X-Webhook-Secret")
        or request.headers.get("X-Metrotherapy-Webhook-Secret")
        or request.query.get("secret")
        or payload.get("secret")
        or ""
    ).strip()


def _max_secret_ok(request: web.Request, payload: dict) -> bool:
    expected = (getattr(settings, "MAX_WEBHOOK_SECRET", "") or "").strip()
    if not expected:
        return _allow_insecure_messenger_webhooks()
    provided = _provided_max_secret(request, payload)
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _vk_secret_ok(payload: dict) -> bool:
    expected = (getattr(settings, "VK_SECRET", "") or "").strip()
    provided = (payload.get("secret") or "").strip()
    if not expected:
        return _allow_insecure_messenger_webhooks()
    if not provided:
        return False
    return hmac.compare_digest(provided, expected)


def _entry_start_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return raw
    lowered = raw.casefold()
    if lowered.startswith("/start ") or lowered.startswith("start "):
        payload = raw.split(maxsplit=1)[1].strip()
        return f"/start {payload}" if payload else "start"
    if lowered.startswith(("bridge_", "ref_", "gift_")):
        return f"/start {raw}"
    return raw


def _claim_replies_if_needed(*, platform: str, extracted: dict) -> tuple[int, list[MessengerReply]] | None:
    text = normalise_messenger_text(extracted["text"])
    token = normalize_gift_token(text)
    if not is_gift_token(token):
        return None
    entry = register_user_entry(
        extracted["user_id"],
        platform=platform,
        external_user_id=extracted["external_user_id"],
        username=extracted["username"],
        display_name=extracted["display_name"],
        first_name=extracted["first_name"],
        start_payload=token,
    )
    result = claim_gift_token(
        gift_token=token,
        recipient_user_id=int(entry.user_id),
        platform=platform,
    )
    return int(entry.user_id), [MessengerReply(text=result.message)]


def _explicit_score_route_text(raw: str | None) -> str | None:
    compact = str(raw or "").strip().casefold().replace("−", "-")
    if compact.startswith("mood:"):
        parts = compact.split(":")
        if len(parts) >= 4 and parts[1] in {"pre", "post"}:
            stage = parts[1]
            sid = parts[2] or "0"
            try:
                score = int(parts[-1])
            except ValueError:
                return None
            if -10 <= score <= 10:
                return f"mood:{stage}:{sid}:{score}"
    if compact in {"score:1", "score=1"}:
        return "+1"
    if compact in {"score:2", "score=2"}:
        return "+2"
    return None


def _max_score_route_text(payload: dict[str, Any]) -> str | None:
    message = payload.get("message") or {}
    body = message.get("body") if isinstance(message, dict) else {}
    if not isinstance(body, dict):
        body = {}
    callback = payload.get("callback") or payload.get("button") or payload.get("payload") or {}
    if not isinstance(callback, dict):
        callback = {}
    candidates = [
        body.get("payload"),
        body.get("button"),
        body.get("callback"),
        message.get("payload") if isinstance(message, dict) else None,
        message.get("button") if isinstance(message, dict) else None,
        message.get("callback") if isinstance(message, dict) else None,
        callback,
        payload.get("payload"),
        payload.get("button"),
        payload.get("callback"),
    ]
    for candidate in candidates:
        score_text = _explicit_score_route_text(text_from_max_payload(candidate))
        if score_text:
            return score_text
    return None


def _vk_score_route_text(payload: dict[str, Any]) -> str | None:
    obj = payload.get("object") or {}
    if not isinstance(obj, dict):
        obj = {}
    message = obj.get("message") or {}
    if not isinstance(message, dict):
        message = {}

    candidates = [
        obj.get("payload"),
        obj.get("button"),
        obj.get("callback"),
        message.get("payload"),
        message.get("button"),
        message.get("callback"),
        payload.get("payload"),
        payload.get("button"),
        payload.get("callback"),
        obj,
        message,
    ]
    for candidate in candidates:
        score_text = _explicit_score_route_text(text_from_vk_payload(candidate))
        if score_text:
            return score_text
    return None


def _vk_dedupe_key(payload: dict[str, Any]) -> str:
    obj = payload.get("object") or {}
    if isinstance(obj, dict):
        event_id = str(obj.get("event_id") or "").strip()
        user_id = str(obj.get("user_id") or obj.get("peer_id") or "").strip()
        if event_id and user_id:
            return f"{event_id}:{user_id}"
        if event_id:
            return event_id
    return vk_event_key(payload)


def _vk_event_context(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    obj = payload.get("object") or {}
    if not isinstance(obj, dict):
        return None
    event_id = str(obj.get("event_id") or "").strip()
    user_id = str(obj.get("user_id") or "").strip()
    peer_id = str(obj.get("peer_id") or user_id).strip()
    if not event_id or not user_id:
        return None
    return event_id, user_id, peer_id


async def _ack_vk_message_event(payload: dict[str, Any]) -> None:
    if not _env_bool("VK_CALLBACK_SNACKBAR_ENABLED", False):
        return
    context = _vk_event_context(payload)
    if context is None:
        return
    event_id, user_id, peer_id = context
    try:
        await VkBotSender().answer_message_event(event_id=event_id, user_id=user_id, peer_id=peer_id)
        log.info("VK message_event acknowledged: user_id=%s event_id=%s", user_id, event_id)
    except MessengerTransportError:
        log.exception("VK message_event acknowledgement failed")


def _process_and_persist(
    *,
    platform: str,
    event_key: str,
    payload: dict[str, Any],
    extracted: dict[str, Any],
    normalized_text: str,
    event_type: str,
) -> tuple[bool, int, int]:
    """Own the synchronous DB/business boundary outside the aiohttp event loop."""

    if not claim_inbound_event(platform, event_key, payload):
        return False, 0, 0

    try:
        log_payload_normalized(
            platform=platform,
            user_id=extracted["user_id"],
            raw_text=extracted["text"],
            normalized_text=normalized_text,
            event_key=event_key,
        )
        claim_result = _claim_replies_if_needed(
            platform=platform,
            extracted={**extracted, "text": normalized_text},
        )
        if claim_result is not None:
            canonical_user_id, replies = claim_result
            action = "gift_claim"
        else:
            canonical_user_id, replies = handle_incoming_text(
                extracted["user_id"],
                platform=platform,
                external_user_id=extracted["external_user_id"],
                text=normalized_text,
                username=extracted["username"],
                display_name=extracted["display_name"],
                first_name=extracted["first_name"],
            )
            action = normalized_text

        log.info(
            "%s %s processed: external_user_id=%s canonical_user_id=%s text=%r replies=%s",
            platform.upper(),
            event_type,
            extracted["external_user_id"],
            canonical_user_id,
            extracted["text"][:120],
            len(replies),
        )
        log_event(
            canonical_user_id,
            f"{platform}_webhook_inbound",
            {"text": extracted["text"][:120], "replies": len(replies), "event_key": event_key},
        )
        persist_reply_bundle(
            platform=platform,
            external_user_id=extracted["external_user_id"],
            canonical_user_id=int(canonical_user_id),
            event_key=event_key,
            replies=list(replies),
            action=action,
        )
        return True, int(canonical_user_id), len(replies)
    except Exception as exc:
        fail_inbound_event(platform, event_key, payload, f"{type(exc).__name__}: {exc}")
        raise


async def vk_webhook(request: web.Request) -> web.Response:
    body = await request.text()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400, text="invalid json")
    if not isinstance(payload, dict):
        return web.Response(status=400, text="bad payload")
    if not _vk_secret_ok(payload):
        log.warning("VK webhook rejected: bad or missing secret")
        return web.Response(status=403, text="forbidden")

    event_type = (payload.get("type") or "").strip()
    if event_type == "confirmation":
        return web.Response(text=(settings.VK_CONFIRMATION_TOKEN or "").strip())
    if event_type not in VK_PROCESSABLE_EVENT_TYPES:
        return web.Response(text="ok")
    if event_type == "message_event":
        await _ack_vk_message_event(payload)

    extracted = extract_vk_message(payload)
    if not extracted:
        return web.Response(text="ok")

    event_key = _vk_dedupe_key(payload)
    normalized_text = _entry_start_text(_vk_score_route_text(payload) or extracted["text"])
    try:
        processed, _, _ = await asyncio.to_thread(
            _process_and_persist,
            platform="vk",
            event_key=event_key,
            payload=payload,
            extracted=extracted,
            normalized_text=normalized_text,
            event_type=event_type,
        )
    except (RuntimeError, OSError, ValueError, TypeError, KeyError):
        log.exception("VK webhook processing failed event_key=%s", event_key)
        return web.Response(status=503, text="retry")
    if not processed:
        log.info("VK webhook duplicate skipped: event_key=%s", event_key)
    return web.Response(text="ok")


_MAX_PROCESSABLE_UPDATE_TYPES = {
    "",
    "message_created",
    "message_callback",
    "bot_started",
    "bot_start",
    "chat_started",
    "conversation_started",
    "button_callback",
    "callback_query",
}


async def max_webhook(request: web.Request) -> web.Response:
    body = await request.text()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        log.warning("MAX webhook rejected invalid json")
        return web.Response(status=400, text="invalid json")
    if not isinstance(payload, dict):
        log.warning("MAX webhook rejected non-object json")
        return web.Response(status=400, text="bad payload")
    if not _max_secret_ok(request, payload):
        log.warning("MAX webhook rejected: bad or missing secret")
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    update_type = (
        payload.get("update_type") or payload.get("type") or payload.get("event_type") or ""
    ).strip()
    if update_type not in _MAX_PROCESSABLE_UPDATE_TYPES:
        log.info("MAX webhook ignored: update_type=%r keys=%s", update_type, sorted(payload.keys()))
        return web.json_response({"ok": True})

    extracted = extract_max_message(payload)
    if not extracted:
        message = payload.get("message") or {}
        body_payload = message.get("body") if isinstance(message, dict) else None
        log.warning(
            "MAX webhook extraction failed: update_type=%r keys=%s message_keys=%s body_type=%s",
            update_type,
            sorted(payload.keys()),
            sorted(message.keys()) if isinstance(message, dict) else [],
            type(body_payload).__name__,
        )
        return web.json_response({"ok": True})

    event_key = max_event_key(payload)
    normalized_text = _entry_start_text(_max_score_route_text(payload) or extracted["text"])
    try:
        processed, _, _ = await asyncio.to_thread(
            _process_and_persist,
            platform="max",
            event_key=event_key,
            payload=payload,
            extracted=extracted,
            normalized_text=normalized_text,
            event_type=update_type,
        )
    except (RuntimeError, OSError, ValueError, TypeError, KeyError):
        log.exception("MAX webhook processing failed event_key=%s", event_key)
        return web.json_response({"ok": False, "error": "retry"}, status=503)
    if not processed:
        log.info("MAX webhook duplicate skipped: update_type=%r event_key=%s", update_type, event_key)
    return web.json_response({"ok": True})
