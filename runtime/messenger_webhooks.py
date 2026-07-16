from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aiogram.exceptions import TelegramAPIError
from aiohttp import web

from config.settings import settings
from runtime.ingress_flags import (
    http_ingress_enabled,
    max_webhook_enabled,
    payment_http_enabled,
    vk_webhook_enabled,
)
from runtime.messenger_ingress import max_webhook, vk_webhook
from runtime.messenger_media_http import audio_access, audio_media
from runtime.payment_http import payment_terms_web, pay_yookassa_web, yookassa_reconciliation_webhook
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
    return web.json_response({"ok": True, "service": "http-ingress"})


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


async def _max_webhook_with_official_secret(request: web.Request) -> web.Response:
    """Map MAX's official secret header onto the stable ingress contract.

    MAX subscriptions deliver the configured secret in
    ``X-Max-Bot-Api-Secret``. The ingress also keeps historical internal header
    aliases for already-configured environments, but new official traffic must
    work without putting secrets in query strings or JSON bodies.
    """

    official = (request.headers.get("X-Max-Bot-Api-Secret") or "").strip()
    legacy_present = any(
        request.headers.get(name)
        for name in (
            "X-Max-Webhook-Secret",
            "X-Webhook-Secret",
            "X-Metrotherapy-Webhook-Secret",
        )
    )
    if official and not legacy_present:
        headers = request.headers.copy()
        headers["X-Max-Webhook-Secret"] = official
        request = request.clone(headers=headers)
    return await max_webhook(request)


def _register_health_routes(app: web.Application) -> None:
    app.router.add_get("/", _health)
    app.router.add_get("/health", _health)
    app.router.add_get("/healthz", _health)


def _register_payment_routes(app: web.Application) -> None:
    app.router.add_get("/terms", payment_terms_web)
    app.router.add_get("/pay/yookassa", pay_yookassa_web)
    app.router.add_post("/pay/yookassa/webhook", yookassa_reconciliation_webhook)


def _register_max_routes(app: web.Application) -> None:
    app.router.add_post("/webhooks/max", _max_webhook_with_official_secret)


def _register_vk_routes(app: web.Application) -> None:
    app.router.add_post("/webhooks/vk", vk_webhook)


def _register_audio_routes(app: web.Application) -> None:
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
    # Legacy /telegram-webhook/{BOT_TOKEN} is disabled by default because bot
    # tokens can leak through access logs and support screenshots. Enable only
    # as a deliberate temporary migration bridge.
    if _truthy_env("TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED"):
        app.router.add_post(telegram_legacy_webhook_path(), telegram_webhook)
    public_url = telegram_public_webhook_url()
    if not public_url:
        raise RuntimeError("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL is required for telegram webhook transport")
    return public_url


def _resolve_ingress_bind(*, ingress_enabled: bool, telegram_enabled: bool) -> tuple[str, int]:
    ingress_host = getattr(settings, "MESSENGER_WEBHOOK_HOST", "127.0.0.1")
    ingress_port = int(getattr(settings, "MESSENGER_WEBHOOK_PORT", 8081))
    telegram_host = getattr(settings, "TELEGRAM_WEBHOOK_HOST", ingress_host)
    telegram_port = int(getattr(settings, "TELEGRAM_WEBHOOK_PORT", ingress_port))

    if ingress_enabled and telegram_enabled and (
        str(ingress_host) != str(telegram_host) or int(ingress_port) != int(telegram_port)
    ):
        raise RuntimeError("Telegram and HTTP ingress runtimes must share the same ingress host/port")

    if telegram_enabled:
        return str(telegram_host), int(telegram_port)
    return str(ingress_host), int(ingress_port)


async def start_messenger_webhook_runtime(
    bot: "Bot | None" = None,
    dispatcher: "Dispatcher | None" = None,
) -> MessengerWebhookRuntime | None:
    payment_enabled = payment_http_enabled()
    max_enabled = max_webhook_enabled()
    vk_enabled = vk_webhook_enabled()
    ingress_enabled = http_ingress_enabled()
    telegram_enabled = telegram_transport() == "webhook"
    if not ingress_enabled and not telegram_enabled:
        return None

    app = web.Application()
    _register_health_routes(app)

    if payment_enabled:
        _register_payment_routes(app)
    if max_enabled:
        _register_max_routes(app)
    if vk_enabled:
        _register_vk_routes(app)
    if max_enabled or vk_enabled:
        _register_audio_routes(app)

    telegram_public_url = ""
    if telegram_enabled:
        telegram_public_url = _register_telegram_routes(app, bot=bot, dispatcher=dispatcher)

    host, port = _resolve_ingress_bind(
        ingress_enabled=ingress_enabled,
        telegram_enabled=telegram_enabled,
    )

    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()

        if telegram_enabled:
            await bot.set_webhook(
                url=telegram_public_url,
                secret_token=(getattr(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "") or "") or None,
                drop_pending_updates=bool(
                    getattr(settings, "TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES", False) or False
                ),
            )
            log.info(
                "Telegram webhook runtime started on %s:%s, public_url=%s",
                host,
                port,
                telegram_public_url,
            )

        log.info(
            "HTTP ingress started on %s:%s payment=%s max=%s vk=%s",
            host,
            port,
            payment_enabled,
            max_enabled,
            vk_enabled,
        )
        return MessengerWebhookRuntime(runner=runner, site=site, telegram_public_url=telegram_public_url)
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError, TelegramAPIError):
        await runner.cleanup()
        raise
