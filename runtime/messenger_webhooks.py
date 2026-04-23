from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher

from config.settings import settings
from runtime.messenger_senders import MaxBotSender, VkBotSender, MessengerTransportError
from services.events import log_event
from services.messenger.audio_delivery import send_next_audio_to_user
from services.messenger.audio_access import register_audio_access
from services.messenger.audio_links import resolve_public_audio_path, AUDIO_MEDIA_PREFIX, AUDIO_ACCESS_PREFIX
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery
from services.messenger.text_ui import handle_incoming_text, MessengerReply
from services.mood_text_flow import complete_pre_score_and_send, complete_post_score_and_send_next
from services.messenger.webhook_dedupe import register_inbound_event

log = logging.getLogger(__name__)


def _stable_payload_key(platform: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8', 'ignore')
    return f'{platform}:sha256:' + hashlib.sha256(encoded).hexdigest()


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _vk_event_key(payload: dict[str, Any]) -> str:
    obj = payload.get('object') or {}
    message = obj.get('message') or obj
    parts = [
        str(payload.get('event_id') or ''),
        str(message.get('id') or message.get('conversation_message_id') or ''),
        str(message.get('from_id') or message.get('user_id') or ''),
        str(message.get('date') or ''),
    ]
    key = ':'.join(part for part in parts if part)
    return key or _stable_payload_key('vk', payload)


def _max_event_key(payload: dict[str, Any]) -> str:
    message = payload.get('message') or {}
    body = message.get('body') or {}
    parts = [
        str(payload.get('update_id') or payload.get('event_id') or ''),
        str(message.get('message_id') or message.get('id') or body.get('mid') or ''),
        str((message.get('sender') or {}).get('user_id') or (message.get('sender') or {}).get('id') or ''),
        str(message.get('created_at') or payload.get('timestamp') or ''),
    ]
    key = ':'.join(part for part in parts if part)
    return key or _stable_payload_key('max', payload)


@dataclass
class MessengerWebhookRuntime:
    runner: web.AppRunner
    site: web.TCPSite
    telegram_public_url: str = ""

    async def stop(self) -> None:
        await self.runner.cleanup()




def _telegram_transport() -> str:
    transport = (getattr(settings, 'TELEGRAM_TRANSPORT', 'polling') or 'polling').strip().lower()
    webhook_enabled = bool(getattr(settings, 'TELEGRAM_WEBHOOK_ENABLED', False) or False)
    if transport == 'webhook':
        return 'webhook'
    if transport == 'polling':
        return 'polling'
    if transport in {'telegram', 'longpoll', 'long-polling'}:
        return 'webhook' if webhook_enabled else 'polling'
    if webhook_enabled:
        return 'webhook'
    return 'polling'


def _telegram_webhook_prefix() -> str:
    prefix = (getattr(settings, 'TELEGRAM_WEBHOOK_PREFIX', '/telegram-webhook') or '/telegram-webhook').strip()
    if not prefix.startswith('/'):
        prefix = '/' + prefix
    return prefix.rstrip('/') or '/telegram-webhook'


def _telegram_webhook_path() -> str:
    return _telegram_webhook_prefix() + '/{bot_token}'


def _telegram_public_webhook_url() -> str:
    base = (getattr(settings, 'TELEGRAM_WEBHOOK_PUBLIC_BASE_URL', '') or '').strip().rstrip('/')
    token = (getattr(settings, 'BOT_TOKEN', '') or '').strip()
    if not base or not token:
        return ''
    return base + _telegram_webhook_prefix() + '/' + token


def _telegram_secret_ok(request: web.Request) -> bool:
    expected = (getattr(settings, 'TELEGRAM_WEBHOOK_SECRET_TOKEN', '') or '').strip()
    if not expected:
        return True
    actual = (request.headers.get('X-Telegram-Bot-Api-Secret-Token') or '').strip()
    if not actual:
        return False
    import hmac
    return hmac.compare_digest(actual, expected)


async def _telegram_webhook(request: web.Request) -> web.Response:
    from aiogram.types import Update

    bot = request.app.get('telegram_bot')
    dispatcher = request.app.get('telegram_dispatcher')
    if bot is None or dispatcher is None:
        raise web.HTTPServiceUnavailable(text='telegram webhook runtime is not configured')

    route_token = (request.match_info.get('bot_token') or '').strip()
    expected_token = (getattr(settings, 'BOT_TOKEN', '') or '').strip()
    if not expected_token or route_token != expected_token:
        raise web.HTTPForbidden(text='bad token')

    if not _telegram_secret_ok(request):
        raise web.HTTPForbidden(text='bad telegram secret')

    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise web.HTTPBadRequest(text='invalid telegram json')
    try:
        update = Update.model_validate(payload, context={'bot': bot})
    except AttributeError:
        update = Update(**payload)
    await dispatcher.feed_webhook_update(bot, update)
    return web.json_response({'ok': True})

def _extract_vk_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    obj = payload.get('object') or {}
    message = obj.get('message') or obj
    from_id = message.get('from_id') or message.get('user_id')
    text = (message.get('text') or '').strip()
    safe_user_id = _safe_int(from_id)
    if safe_user_id is None:
        return None
    return {
        'user_id': safe_user_id,
        'external_user_id': str(from_id),
        'username': None,
        'display_name': None,
        'first_name': None,
        'text': text or 'start',
    }


def _extract_max_message(payload: dict[str, Any]) -> dict[str, Any] | None:
    message = payload.get('message') or {}
    sender = message.get('sender') or {}
    body = message.get('body') or {}
    user_id = sender.get('user_id') or sender.get('id')
    safe_user_id = _safe_int(user_id)
    if safe_user_id is None:
        return None
    text = (body.get('text') or '').strip()
    full_name = ' '.join(part for part in [sender.get('first_name'), sender.get('last_name')] if part).strip() or sender.get('name')
    return {
        'user_id': safe_user_id,
        'external_user_id': str(user_id),
        'username': sender.get('username'),
        'display_name': full_name,
        'first_name': sender.get('first_name') or sender.get('name'),
        'text': text or 'start',
    }


async def _send_reply_bundle(platform: str, external_user_id: str, canonical_user_id: int, replies: list[MessengerReply]) -> None:
    registry = SenderRegistry(max=MaxBotSender(), vk=VkBotSender())
    sender = registry.get(platform)
    if sender is None:
        raise MessengerTransportError(f'No sender for {platform}')
    for reply in replies:
        if reply.kind == 'text':
            await sender.send_text(external_user_id, reply.text)
            continue
        if reply.kind == 'next_audio':
            try:
                result = await send_next_audio_to_user(
                    canonical_user_id,
                    senders=registry,
                    target_platform=platform,
                    fallback=platform,
                )
                if result.transport == 'none':
                    await sender.send_text(external_user_id, result.message)
            except (MessengerTransportError, UnsupportedMessengerDelivery, OSError):
                log.exception('%s cross-channel audio delivery failed', platform.upper())
                await sender.send_text(
                    external_user_id,
                    '⚠️ Не удалось отправить следующее аудио в этот мессенджер. '\
                    'Для MAX/ВКонтакте нужен публичный адрес MESSENGER_PUBLIC_BASE_URL, '\
                    'чтобы бот мог присылать безопасную ссылку на следующий файл.',
                )
            continue
        if reply.kind == 'auto_pre_score':
            result = await complete_pre_score_and_send(
                canonical_user_id,
                platform=platform,
                score=int(reply.meta.get('score') or '0'),
                senders=registry,
            )
            await sender.send_text(external_user_id, result.message)
            continue
        if reply.kind == 'auto_post_score':
            result = await complete_post_score_and_send_next(
                canonical_user_id,
                platform=platform,
                score=int(reply.meta.get('score') or '0'),
                senders=registry,
            )
            await sender.send_text(external_user_id, result.message)
            continue


async def _vk_webhook(request: web.Request) -> web.Response:
    body = await request.text()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400, text='invalid json')
    secret = (settings.VK_SECRET or '').strip()
    if secret and payload.get('secret') not in {'', None, secret}:
        return web.Response(status=403, text='forbidden')

    event_type = (payload.get('type') or '').strip()
    if event_type == 'confirmation':
        return web.Response(text=(settings.VK_CONFIRMATION_TOKEN or '').strip())
    if event_type != 'message_new':
        return web.Response(text='ok')
    if not register_inbound_event('vk', _vk_event_key(payload), payload):
        return web.Response(text='ok')

    extracted = _extract_vk_message(payload)
    if not extracted:
        return web.Response(text='ok')

    canonical_user_id, replies = handle_incoming_text(
        extracted['user_id'],
        platform='vk',
        external_user_id=extracted['external_user_id'],
        text=extracted['text'],
        username=extracted['username'],
        display_name=extracted['display_name'],
        first_name=extracted['first_name'],
    )
    try:
        await _send_reply_bundle('vk', extracted['external_user_id'], canonical_user_id, replies)
    except MessengerTransportError:
        log.exception('VK send failed')
        log_event(canonical_user_id, 'vk_send_failed', {})
    log_event(canonical_user_id, 'vk_webhook_inbound', {'text': extracted['text'][:120]})
    return web.Response(text='ok')


async def _max_webhook(request: web.Request) -> web.Response:
    body = await request.text()
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400, text='invalid json')
    update_type = (payload.get('update_type') or '').strip()
    if update_type and update_type != 'message_created':
        return web.json_response({'ok': True})

    if not register_inbound_event('max', _max_event_key(payload), payload):
        return web.json_response({'ok': True})

    extracted = _extract_max_message(payload)
    if not extracted:
        return web.json_response({'ok': True})

    canonical_user_id, replies = handle_incoming_text(
        extracted['user_id'],
        platform='max',
        external_user_id=extracted['external_user_id'],
        text=extracted['text'],
        username=extracted['username'],
        display_name=extracted['display_name'],
        first_name=extracted['first_name'],
    )
    try:
        await _send_reply_bundle('max', extracted['external_user_id'], canonical_user_id, replies)
    except MessengerTransportError:
        log.exception('MAX send failed')
        log_event(canonical_user_id, 'max_send_failed', {})
    log_event(canonical_user_id, 'max_webhook_inbound', {'text': extracted['text'][:120]})
    return web.json_response({'ok': True})


async def _health(request: web.Request) -> web.Response:
    return web.json_response({'ok': True, 'service': 'messenger-webhooks'})


async def _audio_media(request: web.Request) -> web.StreamResponse:
    filename = request.match_info.get('filename', '')
    path = resolve_public_audio_path(filename)
    if path is None:
        raise web.HTTPNotFound()
    return web.FileResponse(path)


async def _audio_access(request: web.Request) -> web.StreamResponse:
    token = request.match_info.get('token', '')
    grant = register_audio_access(token)
    if grant is None or not grant.file_path.exists() or not grant.file_path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(grant.file_path)


async def start_messenger_webhook_runtime(bot: 'Bot | None' = None, dispatcher: 'Dispatcher | None' = None) -> MessengerWebhookRuntime | None:
    messenger_enabled = bool(getattr(settings, 'MESSENGER_WEBHOOK_ENABLED', False) or False)
    telegram_enabled = _telegram_transport() == 'webhook'
    if not messenger_enabled and not telegram_enabled:
        return None

    app = web.Application()
    app.router.add_get('/', _health)
    app.router.add_get('/health', _health)
    app.router.add_get('/healthz', _health)

    if messenger_enabled:
        app.router.add_post('/webhooks/vk', _vk_webhook)
        app.router.add_post('/webhooks/max', _max_webhook)
        app.router.add_get(f'{AUDIO_MEDIA_PREFIX}{{filename}}', _audio_media)
        app.router.add_get(f'{AUDIO_ACCESS_PREFIX}{{token}}', _audio_access)

    telegram_public_url = ''
    if telegram_enabled:
        if bot is None or dispatcher is None:
            raise RuntimeError('Telegram webhook transport requires bot and dispatcher')
        app['telegram_bot'] = bot
        app['telegram_dispatcher'] = dispatcher
        app.router.add_post(_telegram_webhook_path(), _telegram_webhook)
        telegram_public_url = _telegram_public_webhook_url()
        if not telegram_public_url:
            raise RuntimeError('TELEGRAM_WEBHOOK_PUBLIC_BASE_URL is required for telegram webhook transport')

    messenger_host = getattr(settings, 'MESSENGER_WEBHOOK_HOST', '127.0.0.1')
    messenger_port = int(getattr(settings, 'MESSENGER_WEBHOOK_PORT', 8081))
    telegram_host = getattr(settings, 'TELEGRAM_WEBHOOK_HOST', messenger_host)
    telegram_port = int(getattr(settings, 'TELEGRAM_WEBHOOK_PORT', messenger_port))

    if messenger_enabled and telegram_enabled and (str(messenger_host) != str(telegram_host) or int(messenger_port) != int(telegram_port)):
        raise RuntimeError('Telegram and messenger webhook runtimes must share the same ingress host/port')

    if telegram_enabled:
        host = telegram_host
        port = telegram_port
    else:
        host = messenger_host
        port = messenger_port

    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()

        if telegram_enabled:
            await bot.set_webhook(
                url=telegram_public_url,
                secret_token=(getattr(settings, 'TELEGRAM_WEBHOOK_SECRET_TOKEN', '') or '') or None,
                drop_pending_updates=bool(getattr(settings, 'TELEGRAM_WEBHOOK_DROP_PENDING_UPDATES', False) or False),
            )
            log.info('Telegram webhook runtime started on %s:%s, public_url=%s', host, port, telegram_public_url)

        if messenger_enabled:
            log.info('Messenger webhook runtime started on %s:%s', host, port)

        return MessengerWebhookRuntime(runner=runner, site=site, telegram_public_url=telegram_public_url)
    except Exception:  # validator: allow-wide-except
        await runner.cleanup()
        raise
