import logging
import asyncio
import os
from pathlib import Path

# Matplotlib: keep cache inside project to avoid repeated heavy font scans
_MPLCFG = (Path(__file__).resolve().parent / "data" / "mplcache").resolve()
os.environ.setdefault("MPLCONFIGDIR", str(_MPLCFG))

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramConflictError, TelegramNetworkError
from core.logging import setup_logging
setup_logging()

# Silence noisy matplotlib internals; real errors are still logged via exception handlers
logging.getLogger('matplotlib').setLevel(logging.WARNING)
logging.getLogger('matplotlib.font_manager').setLevel(logging.WARNING)
logging.getLogger('matplotlib.category').setLevel(logging.WARNING)

from core.task_manager import TaskManager
from core.telegram_bot import build_bot

log = logging.getLogger(__name__)

async def _safe_answer_callback(cb, *args, **kwargs) -> None:
    """Best-effort callback acknowledgement.

    Telegram callback queries must be answered quickly. Business logic can fail,
    but the UI button must not look frozen for the user.
    """
    try:
        await cb.answer(*args, **kwargs)
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "callback answer skipped",
            extra={
                "err": str(exc),
                "callback_data": getattr(cb, "data", None),
                "from_user": getattr(getattr(cb, "from_user", None), "id", None),
            },
        )



from config.settings import settings

from core.ai.decision_core import DecisionCore

from services.schema import init_db
from services.validator import validate_all
from services.scheduler import start_scheduler
from services.prewarm import prewarm_audio_cache, prewarm_matplotlib_cache
from services.scheduler import stop_scheduler
from services.db_writer import start_db_writer, stop_db_writer
from runtime.messenger_webhooks import start_messenger_webhook_runtime
from runtime.telegram_transport import telegram_transport
from runtime.health_server import start_health_runtime
from services.messenger.setup import build_setup_status

from core.startup_checks import run_startup_checks

from core.middlewares import (
    SoftRateLimitMiddleware,
    StateLogMiddleware,
    InteractionAnalyticsMiddleware,
    TimeInputTraceMiddleware,
    SlowHandlerLogMiddleware,
    QuickAckCallbackMiddleware,
)

# Важно: не называем модуль handlers.settings как `settings`, чтобы не затереть config.settings.settings
from handlers import start
from handlers import (
    start,
    menu,
    text_input,
    payments,
    demo,
    audio,
    admin,
    admin_stats,
    admin_inline,
    share,
    weather,
    info,
    micro,
    settings as settings_router,
    mood,
    diagnostics,
    gift_flow,
    kb_debug,
    messenger_audio,
)


async def create_application():
    webhook_runtime = None
    health_runtime = None

    async def _on_startup(bot: Bot):
        # v15.2: fail fast if critical files/folders are missing
        run_startup_checks(Path(__file__).resolve().parent)
        init_db()
        # Validate content + DB schema contracts (fail fast in prod)
        # strict validation is enabled in prod by default; can be overridden via env
        app_env = os.getenv("APP_ENV", "dev").lower()
        strict_env = os.getenv("VALIDATOR_STRICT")
        strict = (app_env == "prod") if strict_env is None else (strict_env.strip() in {"1","true","yes","on"})
        validate_all(strict=strict)
        messenger_setup = build_setup_status()
        if messenger_setup.missing:
            log.warning('Messenger setup incomplete: %s', ', '.join(messenger_setup.missing))
        if messenger_setup.warnings:
            log.warning('Messenger setup warnings: %s', ' | '.join(messenger_setup.warnings))
        start_db_writer()
        nonlocal webhook_runtime
        start_scheduler(bot)
        try:
            webhook_runtime = await start_messenger_webhook_runtime(bot=bot, dispatcher=dp)
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError):  # validator: allow-wide-except
            webhook_runtime = None
            selected_transport = telegram_transport()
            log.exception('Messenger/Telegram webhook runtime failed to start')
            # Canonical safety rule:
            # - if Telegram is configured for webhook, there is no polling fallback here;
            #   continuing would leave the process alive but unreachable.
            # - in prod, a failed ingress runtime is an unhealthy deployment and must
            #   be fixed explicitly instead of hidden behind a degraded boot.
            if selected_transport == 'webhook' or app_env == 'prod':
                raise
            log.warning('Continuing without optional messenger webhook runtime in non-prod polling mode')

        nonlocal health_runtime
        try:
            health_runtime = await start_health_runtime()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError):  # validator: allow-wide-except
            health_runtime = None
            log.exception('Health runtime failed to start')
            # In production, readiness/health is part of the deployment contract.
            # Continuing without it makes server mixups and port conflicts invisible.
            if app_env == 'prod':
                raise
            log.warning('Continuing without health endpoint in non-prod mode')

        # Prewarm caches (best-effort). These are one-off startup tasks and should not crash the bot.
        try:
            await prewarm_audio_cache(bot)
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError):  # validator: allow-wide-except
            log.exception("Prewarm audio cache failed")

        try:
            await prewarm_matplotlib_cache()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError):  # validator: allow-wide-except
            log.exception("Prewarm matplotlib cache failed")

    async def _on_shutdown(bot: Bot):
        nonlocal webhook_runtime, health_runtime
        if webhook_runtime is not None:
            await webhook_runtime.stop()
            webhook_runtime = None
        if health_runtime is not None:
            await health_runtime.stop()
            health_runtime = None
        await stop_scheduler()
        await stop_db_writer(drain=True)
        await tm.shutdown()

    token = (settings.BOT_TOKEN or "").strip()
    if not token:
        raise SystemExit("BOT_TOKEN is empty. Put it into .env (see .env.example)")

    tm = TaskManager()

    # Sovereignty (optional): initialize DecisionCore singleton (SelfHealingEngine.tick runs via services.scheduler)
    sovereign_enabled = os.getenv('SOVEREIGN_ENABLED', '0').strip() in {'1','true','yes','on'}
    if sovereign_enabled:
        DecisionCore.instance()

    bot = build_bot(token)
    # Prewarm caches on startup hook (no TaskManager.create in v16.1)
    dp = Dispatcher()
    dp.workflow_data["task_manager"] = tm

    dp.startup.register(_on_startup)
    dp.shutdown.register(_on_shutdown)

    # ✅ Лёгкие middleware без изменения UX:
    # 1) мягкий антиспам (не банит, просто просит "секунду")
    # 2) журнал состояния пользователя (диагностика/аналитика)
    # 0.2 сек — достаточно, чтобы убрать "дубль-клики", но не создаёт ощущение "тормозов".
    # Perf diagnostics (does not change UX): logs slow updates.
    thr_ms = int(os.getenv("SLOW_HANDLER_MS", "700"))
    dp.update.middleware(SlowHandlerLogMiddleware(threshold_ms=thr_ms))

    # Make inline buttons feel instant (spinner disappears immediately)
    dp.update.middleware(QuickAckCallbackMiddleware())

    callback_interval_sec = float(
        os.getenv("SOFT_CALLBACK_RATE_LIMIT_SEC", os.getenv("SOFT_RATE_LIMIT_SEC", "0.05")) or "0.05"
    )
    message_interval_sec = float(
        os.getenv("SOFT_MESSAGE_RATE_LIMIT_SEC", os.getenv("SOFT_RATE_LIMIT_SEC", "0.05")) or "0.05"
    )
    dp.update.middleware(
        SoftRateLimitMiddleware(
            callback_interval_sec=callback_interval_sec,
            message_interval_sec=message_interval_sec,
        )
    )
    # Диагностика: логируем, какой хендлер "перехватил" ввод HH:MM.
    dp.update.middleware(TimeInputTraceMiddleware())
    dp.update.middleware(StateLogMiddleware())
    dp.update.middleware(InteractionAnalyticsMiddleware())

    dp.include_router(start.router)
    dp.include_router(menu.router)
    dp.include_router(text_input.router)
    dp.include_router(demo.router)
    dp.include_router(audio.router)
    dp.include_router(payments.router)
    dp.include_router(admin.router)
    dp.include_router(admin_stats.router)
    dp.include_router(admin_inline.router)
    dp.include_router(share.router)
    dp.include_router(weather.router)
    dp.include_router(info.router)
    dp.include_router(micro.router)
    dp.include_router(settings_router.router)
    dp.include_router(mood.router)
    dp.include_router(diagnostics.router)
    dp.include_router(gift_flow.router)
    dp.include_router(kb_debug.router)
    dp.include_router(messenger_audio.router)

    transport = telegram_transport()
    if transport == 'webhook':
        log.info('Telegram transport selected: webhook')
        try:
            await _on_startup(bot)
            await asyncio.Event().wait()
        finally:
            try:
                await bot.delete_webhook(drop_pending_updates=False)
            except TelegramNetworkError:
                log.warning('Failed to clear Telegram webhook during webhook shutdown', exc_info=True)
            await _on_shutdown(bot)
            try:
                await bot.session.close()
            except (RuntimeError, AttributeError):
                log.debug('bot.session.close final close failed', exc_info=True)
        return

    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except TelegramNetworkError:
        log.warning('Failed to clear Telegram webhook before polling start; continuing with polling', exc_info=True)

    # Telegram API connectivity can be temporarily unavailable (DNS issues, network hiccups,
    # captive portal, ISP blocks, etc.). aiogram may raise TelegramNetworkError during the
    # initial getMe() call while starting polling. We retry with backoff, but not forever.
    backoff = 2
    max_retries = max(1, int(os.getenv('STARTUP_NETWORK_RETRIES', '5')))
    attempt = 0
    try:
        while True:
            try:
                await dp.start_polling(bot)
                break
            except TelegramConflictError as e:
                log.exception('Telegram polling conflict: another bot instance is already consuming updates')
                raise SystemExit(
                    'Telegram polling conflict: another instance is already running. '
                    'Stop the old systemd/local process or switch this deployment to webhook mode.'
                ) from e
            except TelegramNetworkError as e:
                attempt += 1
                msg = str(e)
                low = msg.lower()
                if 'getaddrinfo failed' in low or 'name or service not known' in low or 'gaierror' in low:
                    log.error(
                        'Проблема DNS/интернет, а не код. Подсказки: проверь интернет, DNS (1.1.1.1/8.8.8.8), ' +
                        'ipconfig /flushdns и доступ к api.telegram.org (nslookup). При блокировках нужен VPN/Proxy.'
                    )

                log.exception('TelegramNetworkError while starting polling (attempt %s/%s).', attempt, max_retries)
                try:
                    await bot.session.close()
                except (RuntimeError, AttributeError):
                    log.debug('bot.session.close failed', exc_info=True)
                if attempt >= max_retries:
                    raise SystemExit(
                        f'Не удалось подключиться к Telegram после {attempt} попыток. '                         f'Проверь сеть/DNS/прокси или увеличь STARTUP_NETWORK_RETRIES.'
                    ) from e
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
    finally:
        try:
            await bot.session.close()
        except (RuntimeError, AttributeError):
            log.debug('bot.session.close final close failed', exc_info=True)
