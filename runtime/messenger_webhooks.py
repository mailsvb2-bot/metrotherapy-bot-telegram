from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiohttp import web

from config.settings import settings
from runtime.messenger_ingress import max_webhook, vk_webhook
from runtime.messenger_media_http import audio_access, audio_media
from runtime.payment_http import pay_yookassa_web, yookassa_reconciliation_webhook
from runtime.telegram_transport import telegram_transport
from runtime.telegram_webhook_runtime import (
    telegram_legacy_webhook_path,
    telegram_public_webhook_url,
    telegram_webhook,
    telegram_webhook_path,
)
from services.messenger.audio_links import AUDIO_ACCESS_PREFIX, AUDIO_MEDIA_PREFIX

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher

log = logging.getLogger(__name__)


@dataclass
class MessengerWebhookRuntime:
    runner: web.AppRunner
    site: web.TCPSite
    telegram_public_url: str = ""

    async def stop(self) -> None:
        await self.runner.cleanup()


async def _health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "messenger-webhooks"})


def _register_health_routes(app: web.Application) -> None:
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    app.router.add_get("/healthz", _health)


def _register_messenger_routes(app: web.Application) -> None:
    app.router.add_get("/pay/yookassa", pay_yookassa_web)
    app.router.add_post("/pay/yookassa/webhook", yookassa_reconciliation_webhook)
    app.router.add_post("/webhooks/vk", vk_webhook)
    app.router.add_post("/webhooks/max", max_webhook)
    app.router.add_get(f"{AUDIO_MEDIA_PREFIX}{{filename}}", audio_media)
    app.router.add_get(f"{AUDIO_ACCESS_PREFIX}{{token}}", audio_access)


def _register_telegram_routes(
    app: web.Application,
    *,
    bot: "Bot | None",
    dispatcher: "Dispatcher | None",
) -> str:
    if bot is None or dispatcher is None:
        raise RuntimeError("Telegram webhook transport requires bot and dispatcher")
    app["telegram_bot"] = bot
    app["telegram_dispatcher"] = dispatcher
    app["task_manager"] = dispatcher.workflow_data.get("task_manager")
    app.router.add_post(telegram_webhook_path(), telegram_webhook)
    # Transitional compatibility: older deployments used /telegram-webhook/{BOT_TOKEN}.
    # The public URL now points to the tokenless route, but this keeps existing
    # reverse-proxy/server snippets working until they are updated.
    app.router.add_post(telegram_legacy_webhook_path(), telegram_webhook)
    public_url = telegram_public_webhook_url()
    if not public_url:
        raise RuntimeError("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL is required for telegram webhook transport")
    return public_url


def _resolve_ingress_bind(*, messenger_enabled: bool, telegram_enabled: bool) -> tuple[str, int]:
    messenger_host = getattr(settings, "MESSENGER_WEBHOOK_HOST", "127.0.0.1")
    messenger_port = int(getattr(settings, "MESSENGER_WEBHOOK_PORT", 8081))
    telegram_host = getattr(settings, "TELEGRAM_WEBHOOK_HOST", messenger_host)
    telegram_port = int(getattr(settings, "TELEGRAM_WEBHOOK_PORT", messenger_port))

    if messenger_enabled and telegram_enabled and (
        str(messenger_host) != str(telegram_host) or int(messenger_port) != int(telegram_port)
    ):
        raise RuntimeError("Telegram and messenger webhook runtimes must share the same ingress host/port")

    if telegram_enabled:
        return str(telegram_host), int(telegram_port)
    return str(messenger_host), int(messenger_port)


async def start_messenger_webhook_runtime(
    bot: "Bot | None" = None,
    dispatcher: "Dispatcher | None" = None,
) -> MessengerWebhookRuntime | None:
    messenger_enabled = bool(getattr(settings, "MESSENGER_WEBHOOK_ENABLED", False) or False)
    telegram_enabled = telegram_transport() == "webhook"
    if not messenger_enabled and not telegram_enabled:
        return None

    app = web.Application()
    _register_health_routes(app)

    if messenger_enabled:
        _register_messenger_routes(app)

    telegram_public_url = ""
    if telegram_enabled:
        telegram_public_url = _register_telegram_routes(app, bot=bot, dispatcher=dispatcher)

    host, port = _resolve_ingress_bind(messenger_enabled=messenger_enabled, telegram_enabled=telegram_enabled)

    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()

        if telegram_enabled:
            await bot.set_webhook(
                url=telegram_public_url,
                secret_token=(getattr(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "") or "") or None,
                drop_pending_updates=bool(getattr(settings, "TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES", False) or False),
            )
            log.info("Telegram webhook runtime started on %s:%s, public_url=%s", host, port, telegram_public_url)

        if messenger_enabled:
            log.info("Messenger webhook runtime started on %s:%s", host, port)

        return MessengerWebhookRuntime(runner=runner, site=site, telegram_public_url=telegram_public_url)
    except Exception:  # validator: allow-wide-except
        await runner.cleanup()
        raise