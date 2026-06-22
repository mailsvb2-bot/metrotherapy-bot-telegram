from __future__ import annotations

import hmac
import json
import os
from typing import TYPE_CHECKING

from aiohttp import web

from config.settings import settings

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _app_env() -> str:
    return (os.getenv("APP_ENV") or getattr(settings, "APP_ENV", "") or "dev").strip().lower()


def _allow_insecure_telegram_webhook() -> bool:
    # Explicit local/dev escape hatch only. Production/staging webhooks must be authenticated.
    if _app_env() in {"prod", "production", "stage", "staging"}:
        return False
    return _env_bool("ALLOW_INSECURE_TELEGRAM_WEBHOOK", False)


def telegram_webhook_prefix() -> str:
    prefix = (getattr(settings, "TELEGRAM_WEBHOOK_PREFIX", "/telegram-webhook") or "/telegram-webhook").strip()
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    return prefix.rstrip("/") or "/telegram-webhook"


def telegram_webhook_path() -> str:
    """Canonical tokenless webhook path.

    Telegram's X-Telegram-Bot-Api-Secret-Token header is the authentication
    mechanism. Keeping BOT_TOKEN in URLs leaks it into proxy/access logs, shell
    history and monitoring. The legacy token path is still registered separately
    by the ingress runtime for a safe transition.
    """
    return telegram_webhook_prefix()


def telegram_legacy_webhook_path() -> str:
    """Backward-compatible path used by older nginx/server snippets."""
    return telegram_webhook_prefix() + "/{bot_token}"


def telegram_public_webhook_url() -> str:
    base = (getattr(settings, "TELEGRAM_WEBHOOK_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        return ""
    return base + telegram_webhook_path()


def telegram_secret_ok(request: web.Request) -> bool:
    expected = (getattr(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "") or "").strip()
    if not expected:
        return _allow_insecure_telegram_webhook()
    actual = (request.headers.get("X-Telegram-Bot-Api-Secret-Token") or "").strip()
    if not actual:
        return False
    return hmac.compare_digest(actual, expected)


async def telegram_webhook(request: web.Request) -> web.Response:
    from aiogram.types import Update

    bot = request.app.get("telegram_bot")
    dispatcher = request.app.get("telegram_dispatcher")
    task_manager = request.app.get("task_manager")
    if bot is None or dispatcher is None:
        raise web.HTTPServiceUnavailable(text="telegram webhook runtime is not configured")

    route_token = (request.match_info.get("bot_token") or "").strip()
    if route_token:
        expected_token = (getattr(settings, "BOT_TOKEN", "") or "").strip()
        if not expected_token or route_token != expected_token:
            raise web.HTTPForbidden(text="bad token")

    if not telegram_secret_ok(request):
        raise web.HTTPForbidden(text="bad telegram secret")

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text="invalid telegram json")
    try:
        update = Update.model_validate(payload, context={"bot": bot})
    except AttributeError:
        update = Update(**payload)

    async def _process_update() -> None:
        await dispatcher.feed_webhook_update(bot, update)

    if task_manager is not None:
        task_manager.create(_process_update(), name="telegram-webhook-update")
    else:
        await _process_update()
    return web.json_response({"ok": True})
