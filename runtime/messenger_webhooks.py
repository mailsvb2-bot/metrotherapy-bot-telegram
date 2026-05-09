from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import urllib.parse
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from aiohttp import web

if TYPE_CHECKING:
    from aiogram import Bot, Dispatcher

from config.settings import settings
from runtime.telegram_transport import telegram_transport
from runtime.messenger_senders import MaxBotSender, VkBotSender, MessengerTransportError
from services.events import log_event
from services.weather import get_weather_text_async, set_city
from services.db import db
from services.messenger.audio_delivery import send_next_audio_to_user, _post_audio_control_kwargs
from services.messenger.audio_access import register_audio_access
from services.messenger.audio_links import resolve_public_audio_path, AUDIO_MEDIA_PREFIX, AUDIO_ACCESS_PREFIX
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery
from services.messenger.text_ui import handle_incoming_text, MessengerReply
from services.mood_text_flow import complete_pre_score_and_send, complete_post_score_and_send_next
from services.messenger.webhook_dedupe import register_inbound_event
from services.payments.yookassa_checkout import create_yookassa_confirmation_url
from runtime.messenger_payloads import (
    extract_max_message as _extract_max_message,
    extract_vk_message as _extract_vk_message,
    max_event_key as _max_event_key,
    normalise_messenger_text as _normalise_messenger_text,
    safe_int as _safe_int,
    stable_payload_key as _stable_payload_key,
    text_from_vk_payload as _text_from_vk_payload,
    vk_event_key as _vk_event_key,
)
from runtime.messenger_vk_ui import (
    vk_default_keyboard_json as _vk_default_keyboard_json,
    vk_demo_kind_keyboard_json as _vk_demo_kind_keyboard_json,
    vk_score_scale_keyboard_json as _vk_score_scale_keyboard_json,
    vk_text_send_kwargs as _vk_text_send_kwargs,
    vk_weather_city_keyboard_json as _vk_weather_city_keyboard_json,
    vk_weather_keyboard_json as _vk_weather_keyboard_json,
    with_vk_keyboard as _with_vk_keyboard,
)

log = logging.getLogger(__name__)


@dataclass
class MessengerWebhookRuntime:
    runner: web.AppRunner
    site: web.TCPSite
    telegram_public_url: str = ""

    async def stop(self) -> None:
        await self.runner.cleanup()


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
    task_manager = request.app.get('task_manager')
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

    async def _process_update() -> None:
        await dispatcher.feed_webhook_update(bot, update)

    if task_manager is not None:
        task_manager.create(_process_update(), name='telegram-webhook-update')
    else:
        await _process_update()
    return web.json_response({'ok': True})


def _vk_progress_chart_path(user_id: int) -> Path | None:
    """Build VK progress chart from canonical mood_sessions."""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, day, slot, kind, anchor_id, pre_score, post_score, audio_sent
            FROM mood_sessions
            WHERE user_id=?
              AND (pre_score IS NOT NULL OR post_score IS NOT NULL)
            ORDER BY id ASC
            LIMIT 80
            """,
            (int(user_id),),
        ).fetchall()

    records = [dict(r) for r in rows]
    if not records:
        return None

    labels = []
    pre_values = []
    post_values = []
    delta_values = []

    for idx, row in enumerate(records, start=1):
        anchor = row.get("anchor_id")
        day = str(row.get("day") or "")
        label = f"№{anchor}" if anchor not in (None, "") else str(idx)
        if day:
            label = f"{label}\n{day[-5:]}"
        labels.append(label)

        pre = row.get("pre_score")
        post = row.get("post_score")
        pre_f = float(pre) if pre is not None else None
        post_f = float(post) if post is not None else None

        pre_values.append(pre_f)
        post_values.append(post_f)
        delta_values.append((post_f - pre_f) if pre_f is not None and post_f is not None else None)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        log.exception("VK progress chart: matplotlib unavailable")
        return None

    out_dir = Path("/tmp/metrotherapy_vk_charts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"progress_{int(user_id)}.png"

    x = list(range(1, len(labels) + 1))
    fig, ax = plt.subplots(figsize=(10, 5.5))

    if any(v is not None for v in pre_values):
        ax.plot(x, [v if v is not None else float("nan") for v in pre_values], marker="o", label="До")
    if any(v is not None for v in post_values):
        ax.plot(x, [v if v is not None else float("nan") for v in post_values], marker="o", label="После")
    if any(v is not None for v in delta_values):
        ax.bar(x, [v if v is not None else 0 for v in delta_values], alpha=0.25, label="Изменение")

    ax.axhline(0, linewidth=1)
    ax.set_title("Метротерапия — динамика состояния")
    ax.set_ylabel("Оценка состояния от -10 до +10")
    ax.set_xlabel("Практики")
    ax.set_ylim(-10.5, 10.5)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(loc="best")

    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)

    log.info("VK progress chart built: user_id=%s path=%s", user_id, out_path)
    return out_path


async def _send_reply_bundle(platform: str, external_user_id: str, canonical_user_id: int, replies: list[MessengerReply]) -> None:
    registry = SenderRegistry(max=MaxBotSender(), vk=VkBotSender())
    sender = registry.get(platform)
    if sender is None:
        raise MessengerTransportError(f'No sender for {platform}')
    for reply in replies:
        if reply.kind == 'text':
            kwargs: dict[str, Any] = {}
            if platform == 'vk':
                keyboard_kind = (reply.meta or {}).get('vk_keyboard')
                if keyboard_kind == 'demo_kind':
                    kwargs['keyboard_json'] = _vk_demo_kind_keyboard_json()
                elif keyboard_kind == 'score_scale':
                    kwargs['keyboard_json'] = _vk_score_scale_keyboard_json()
                elif keyboard_kind == 'weather':
                    kwargs['keyboard_json'] = _vk_weather_keyboard_json()
                elif keyboard_kind == 'weather_city':
                    kwargs['keyboard_json'] = _vk_weather_city_keyboard_json()
            await sender.send_text(external_user_id, reply.text, **_with_vk_keyboard(platform, kwargs))
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
                    await sender.send_text(external_user_id, result.message, **_with_vk_keyboard(platform, {}))
            except (MessengerTransportError, UnsupportedMessengerDelivery, OSError):
                log.exception('%s cross-channel audio delivery failed', platform.upper())
                await sender.send_text(
                    external_user_id,
                    '⚠️ Не удалось отправить следующее аудио в этот мессенджер. '
                    'Для MAX/ВКонтакте нужен публичный адрес MESSENGER_PUBLIC_BASE_URL, '
                    'чтобы бот мог присылать безопасную ссылку на следующий файл.',
                    **_with_vk_keyboard(platform, {}),
                )
            continue
        if reply.kind == 'weather_show':
            txt = await get_weather_text_async(canonical_user_id, timeout_sec=2.0)
            await sender.send_text(
                external_user_id,
                txt + "\n\nМожно нажать «🏙 Изменить город» или отправить команду: город.",
                **_with_vk_keyboard(platform, {"keyboard_json": _vk_weather_keyboard_json()} if platform == "vk" else {}),
            )
            continue

        if reply.kind == 'weather_set_city':
            city = (reply.meta or {}).get("city", "").strip()
            if not city:
                await sender.send_text(
                    external_user_id,
                    "Пожалуйста, напишите название города текстом.",
                    **_with_vk_keyboard(platform, {"keyboard_json": _vk_weather_city_keyboard_json()} if platform == "vk" else {}),
                )
                continue

            ok, info = await asyncio.to_thread(set_city, canonical_user_id, city)
            if not ok:
                await sender.send_text(
                    external_user_id,
                    "❌ " + str(info),
                    **_with_vk_keyboard(platform, {"keyboard_json": _vk_weather_city_keyboard_json()} if platform == "vk" else {}),
                )
                continue

            log_event(canonical_user_id, "weather_city_set", {"city": str(info), "platform": platform})
            txt = await get_weather_text_async(canonical_user_id, timeout_sec=2.0)
            await sender.send_text(
                external_user_id,
                f"✅ Город принят: {info}.\n\n{txt}",
                **_with_vk_keyboard(platform, {"keyboard_json": _vk_weather_keyboard_json()} if platform == "vk" else {}),
            )
            continue

        if reply.kind == 'progress_chart':
            chart_path = _vk_progress_chart_path(canonical_user_id)
            if chart_path is None:
                await sender.send_text(
                    external_user_id,
                    '📈 Пока недостаточно данных для графика. Пройдите цикл: шкала ДО → аудио → Прослушал → шкала ПОСЛЕ.',
                    **_with_vk_keyboard(platform, {}),
                )
                continue

            try:
                await sender.send_audio_file(
                    external_user_id,
                    chart_path,
                    caption='📈 Ваш график прогресса Метротерапии',
                    **_with_vk_keyboard(platform, {}),
                )
                log.info('%s progress chart sent: user_id=%s path=%s', platform.upper(), canonical_user_id, chart_path)
            except Exception:
                log.exception('%s progress chart send failed', platform.upper())
                await sender.send_text(
                    external_user_id,
                    '⚠️ График построен, но не удалось отправить его во ВКонтакте.',
                    **_with_vk_keyboard(platform, {}),
                )
            continue

        if reply.kind == 'auto_pre_score':
            result = await complete_pre_score_and_send(
                canonical_user_id,
                platform=platform,
                score=int(reply.meta.get('score') or '0'),
                senders=registry,
            )
            kwargs: dict[str, Any] = {}
            if platform == 'vk' and getattr(result, 'prompt_done', False):
                kwargs.update(_post_audio_control_kwargs('vk'))
            await sender.send_text(external_user_id, result.message, **_with_vk_keyboard(platform, kwargs))
            continue
        if reply.kind == 'auto_post_score':
            result = await complete_post_score_and_send_next(
                canonical_user_id,
                platform=platform,
                score=int(reply.meta.get('score') or '0'),
                senders=registry,
            )
            await sender.send_text(external_user_id, result.message, **_with_vk_keyboard(platform, {}))
            continue


def _env_value(name: str, default: str = "") -> str:
    return (os.environ.get(name) or getattr(settings, name, default) or default).strip()


def _payment_public_base_url() -> str:
    return _env_value("PAYMENT_PUBLIC_BASE_URL", _env_value("MESSENGER_PUBLIC_BASE_URL", "https://metrotherapy-bot.metrotherapy.ru")).rstrip("/")


def _payment_link(*, source: str, external_user_id: str, kind: str = "subscription") -> str:
    query = urllib.parse.urlencode(
        {
            "source": source,
            "user_id": external_user_id,
            "kind": kind,
        }
    )
    return f"{_payment_public_base_url()}/pay/yookassa?{query}"


def _yookassa_amount(kind: str) -> str:
    raw = _env_value("GIFT_PAYMENT_AMOUNT_RUB" if kind == "gift" else "PAYMENT_AMOUNT_RUB", "990")
    raw = raw.replace(",", ".").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 990.0
    return f"{value:.2f}"


def _yookassa_description(kind: str) -> str:
    if kind == "gift":
        return _env_value("GIFT_PAYMENT_DESCRIPTION", "Метротерапия — подарок")[:128]
    return _env_value("PAYMENT_DESCRIPTION", "Метротерапия — доступ к аудиопрактикам")[:128]


def _yookassa_return_url(kind: str, source: str) -> str:
    base = _env_value("PAYMENT_RETURN_URL", _env_value("SITE_PUBLIC_URL", "https://metrotherapy.ru")).rstrip("/")
    return f"{base}?payment=return&kind={urllib.parse.quote(kind)}&source={urllib.parse.quote(source)}"


def _create_yookassa_payment(*, source: str, external_user_id: str, kind: str = "subscription", **_: object) -> str:
    return create_yookassa_confirmation_url(
        source=source,
        external_user_id=external_user_id,
        kind=kind,
    )


async def _pay_yookassa_web(request: web.Request) -> web.Response:
    source = (request.query.get("source") or "unknown").strip()[:32]
    external_user_id = (request.query.get("user_id") or "").strip()[:64]
    kind = (request.query.get("kind") or "subscription").strip().casefold()
    if kind not in {"subscription", "gift"}:
        kind = "subscription"

    try:
        confirmation_url = await asyncio.to_thread(
            _create_yookassa_payment,
            source=source,
            external_user_id=external_user_id,
            kind=kind,
        )
    except Exception as exc:
        log.exception("YooKassa web payment endpoint failed")
        return web.Response(
            status=500,
            text=(
                "Не удалось создать платёж YooKassa. "
                "Проверьте YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY и доступ сервера к api.yookassa.ru. "
                f"Ошибка: {type(exc).__name__}"
            ),
            content_type="text/plain",
        )

    raise web.HTTPFound(location=confirmation_url)


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
        text=_normalise_messenger_text(extracted['text']),
        username=extracted['username'],
        display_name=extracted['display_name'],
        first_name=extracted['first_name'],
    )

    log.info(
        'VK message_new processed: external_user_id=%s canonical_user_id=%s text=%r replies=%s',
        extracted['external_user_id'],
        canonical_user_id,
        extracted['text'][:120],
        len(replies),
    )

    try:
        await _send_reply_bundle('vk', extracted['external_user_id'], canonical_user_id, replies)
        log.info(
            'VK replies sent: external_user_id=%s canonical_user_id=%s replies=%s',
            extracted['external_user_id'],
            canonical_user_id,
            len(replies),
        )
    except MessengerTransportError:
        log.exception('VK send failed')
        log_event(canonical_user_id, 'vk_send_failed', {})
    log_event(canonical_user_id, 'vk_webhook_inbound', {'text': extracted['text'][:120], 'replies': len(replies)})
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
        text=_normalise_messenger_text(extracted['text']),
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
    telegram_enabled = telegram_transport() == 'webhook'
    if not messenger_enabled and not telegram_enabled:
        return None

    app = web.Application()
    app.router.add_get('/', _health)
    app.router.add_get('/health', _health)
    app.router.add_get('/healthz', _health)

    if messenger_enabled:
        app.router.add_get('/pay/yookassa', _pay_yookassa_web)
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
        app['task_manager'] = dispatcher.workflow_data.get('task_manager')
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
