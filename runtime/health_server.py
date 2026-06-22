from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from config.settings import settings
from core.paths import DB_PATH, ROOT
from runtime.telegram_transport import telegram_transport
from services.ai.policy import ai_policy_snapshot
from services.db import get_connection
from services.db.runtime import CONFIG, redacted_db_target
from services.db.schema.readiness import required_readiness_tables, schema_readiness
from services.scheduler import scheduler_health_snapshot

log = logging.getLogger(__name__)


@dataclass
class HealthRuntime:
    runner: web.AppRunner
    site: web.TCPSite

    async def stop(self) -> None:
        await self.runner.cleanup()


def _scheduler_snapshot() -> dict[str, Any]:
    try:
        return scheduler_health_snapshot()
    except (ImportError, AttributeError, RuntimeError):
        return {
            'scheduler_loop_task_running': False,
            'precise_scheduler_running': False,
            'precise_scheduler_task_running': False,
            'precise_scheduler_queue_size': 0,
        }


def _messenger_webhook_configured() -> bool:
    try:
        return bool(getattr(settings, 'MESSENGER_WEBHOOK_ENABLED', False) or False)
    except (AttributeError, RuntimeError):
        return False


def _telegram_transport() -> str:
    try:
        return telegram_transport()
    except (AttributeError, RuntimeError):
        return 'unknown'


def _telegram_webhook_configured() -> bool:
    return _telegram_transport() == 'webhook'


def _webhook_configured() -> bool:
    return bool(_messenger_webhook_configured() or _telegram_webhook_configured())


def _db_ready() -> tuple[bool, str | None]:
    try:
        with get_connection() as conn:
            conn.execute('SELECT 1').fetchone()
        return True, None
    except Exception as exc:  # validator: allow-wide-except
        return False, f'db:{exc}'


def _schema_ready() -> tuple[bool, str | None]:
    return schema_readiness()


def _storage_health_fields() -> dict[str, Any]:
    fields: dict[str, Any] = {'root_exists': False}
    try:
        fields['root_exists'] = ROOT.exists()
        if CONFIG.uses_postgres:
            fields['legacy_sqlite_path'] = str(DB_PATH)
            fields['legacy_sqlite_present'] = Path(DB_PATH).exists()
        else:
            fields['db_path'] = str(DB_PATH)
            fields['db_exists'] = Path(DB_PATH).exists()
    except OSError:
        fields['root_exists'] = False
        if CONFIG.uses_postgres:
            fields['legacy_sqlite_path'] = str(DB_PATH)
            fields['legacy_sqlite_present'] = False
        else:
            fields['db_path'] = str(DB_PATH)
            fields['db_exists'] = False
    return fields


def build_health_payload() -> tuple[dict[str, Any], int]:
    scheduler = _scheduler_snapshot()
    telegram_transport_value = _telegram_transport()
    messenger_webhook_enabled = _messenger_webhook_configured()
    telegram_webhook_enabled = telegram_transport_value == 'webhook'
    webhook_runtime_enabled = bool(messenger_webhook_enabled or telegram_webhook_enabled)
    details: dict[str, Any] = {
        'ok': True,
        'service': 'metrotherapy',
        'probe': 'health',
        'db_engine': CONFIG.engine,
        'db_target': redacted_db_target(),
        'telegram_transport': telegram_transport_value,
        'telegram_webhook_enabled': telegram_webhook_enabled,
        'messenger_webhook_enabled': messenger_webhook_enabled,
        'webhook_runtime_enabled': webhook_runtime_enabled,
        'app_env': (os.getenv('APP_ENV', 'dev') or 'dev').strip().lower(),
        **_storage_health_fields(),
        **ai_policy_snapshot(),
        **scheduler,
    }
    return details, 200


def build_readiness_payload() -> tuple[dict[str, Any], int]:
    db_ok, db_error = _db_ready()
    schema_ok, schema_error = _schema_ready()
    scheduler = _scheduler_snapshot()
    telegram_transport_value = _telegram_transport()
    messenger_webhook_enabled = _messenger_webhook_configured()
    telegram_webhook_enabled = telegram_transport_value == 'webhook'
    webhook_runtime_enabled = bool(messenger_webhook_enabled or telegram_webhook_enabled)
    app_env = (os.getenv('APP_ENV', 'dev') or 'dev').strip().lower()
    scheduler_ok = bool(scheduler.get('scheduler_loop_task_running'))
    webhook_ok = True
    if app_env in {'prod', 'production'} and telegram_webhook_enabled:
        webhook_ok = webhook_runtime_enabled
    errors: list[str] = []
    if db_error is not None:
        errors.append(db_error)
    if schema_error is not None:
        errors.append(schema_error)
    if not scheduler_ok:
        errors.append('scheduler:not_running')
    if not webhook_ok:
        errors.append('webhook:not_ready')
    ready = bool(db_ok and schema_ok and scheduler_ok and webhook_ok)
    details: dict[str, Any] = {
        'ok': ready,
        'service': 'metrotherapy',
        'probe': 'readiness',
        'db_ready': db_ok,
        'schema_ready': schema_ok,
        'scheduler_ready': scheduler_ok,
        'webhook_ready': webhook_ok,
        'required_tables': required_readiness_tables(),
        'db_engine': CONFIG.engine,
        'db_target': redacted_db_target(),
        'telegram_transport': telegram_transport_value,
        'telegram_webhook_enabled': telegram_webhook_enabled,
        'messenger_webhook_enabled': messenger_webhook_enabled,
        'webhook_runtime_enabled': webhook_runtime_enabled,
        'app_env': app_env,
        **_storage_health_fields(),
        **ai_policy_snapshot(),
        **scheduler,
    }
    if errors:
        details['error'] = ';'.join(errors)
        return details, 500
    return details, 200


async def _health(request: web.Request) -> web.Response:
    payload, status = build_health_payload()
    return web.json_response(payload, status=status)


async def _ready(request: web.Request) -> web.Response:
    payload, status = build_readiness_payload()
    return web.json_response(payload, status=status)


async def start_health_runtime() -> HealthRuntime | None:
    enabled = (getattr(settings, 'HEALTHCHECK_ENABLED', True) or False)
    if not enabled:
        return None
    host = getattr(settings, 'HEALTHCHECK_HOST', '127.0.0.1')
    port = int(getattr(settings, 'HEALTHCHECK_PORT', 8082))
    app = web.Application()
    app.router.add_get('/health', _health)
    app.router.add_get('/healthz', _health)
    app.router.add_get('/readyz', _ready)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        log.info('Health runtime started on %s:%s', host, port)
        return HealthRuntime(runner=runner, site=site)
    except Exception:  # validator: allow-wide-except
        await runner.cleanup()
        raise
