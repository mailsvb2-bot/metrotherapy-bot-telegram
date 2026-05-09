from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from config.settings import settings
from runtime.telegram_transport import telegram_transport
from core.paths import DB_PATH, ROOT
from services.db.runtime import CONFIG, redacted_db_target
from services.db import get_connection
from services.scheduler import scheduler_health_snapshot

log = logging.getLogger(__name__)


@dataclass
class HealthRuntime:
    runner: web.AppRunner
    site: web.TCPSite

    async def stop(self) -> None:
        await self.runner.cleanup()



def _scheduler_snapshot() -> dict[str, bool | int]:
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
    """Return whether any local webhook ingress runtime should be up.

    Kept as the backward-compatible aggregate health helper. The health payload
    now also exposes Telegram and messenger webhook states separately so the
    production-safe hybrid mode is not ambiguous:
    Telegram polling + MAX/VK webhook runtime.
    """
    return bool(_messenger_webhook_configured() or _telegram_webhook_configured())



def _db_ready() -> tuple[bool, str | None]:
    try:
        with get_connection() as conn:
            conn.execute('SELECT 1').fetchone()
        return True, None
    except Exception as exc:  # validator: allow-wide-except
        return False, f'db:{exc}'


def _schema_ready() -> tuple[bool, str | None]:
    required_tables = {'users', 'jobs'}
    try:
        with get_connection() as conn:
            if CONFIG.uses_postgres:
                rows = conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('users','jobs')"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('users','jobs')"
                ).fetchall()
        names: set[str] = set()
        for row in rows:
            if isinstance(row, dict):
                value = row.get('table_name') or row.get('name')
            else:
                try:
                    value = row[0]
                except Exception:  # validator: allow-wide-except
                    value = None
            if value:
                names.add(str(value))
        missing = sorted(required_tables - names)
        if missing:
            return False, 'schema_missing:' + ','.join(missing)
        return True, None
    except Exception as exc:  # validator: allow-wide-except
        return False, f'schema:{exc}'



def _storage_health_fields() -> dict[str, Any]:
    """Expose active storage unambiguously in health output.

    In Postgres mode, the historical SQLite file path is still useful as a
    migration/legacy signal, but it must not look like the active database.
    """
    fields: dict[str, Any] = {
        'root_exists': False,
    }
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
    db_ok, db_error = _db_ready()
    schema_ok, schema_error = _schema_ready()
    scheduler = _scheduler_snapshot()
    telegram_transport_value = _telegram_transport()
    messenger_webhook_enabled = _messenger_webhook_configured()
    telegram_webhook_enabled = telegram_transport_value == 'webhook'
    webhook_runtime_enabled = bool(messenger_webhook_enabled or telegram_webhook_enabled)
    details: dict[str, Any] = {
        'ok': bool(db_ok and schema_ok),
        'service': 'metrotherapy',
        'db_ready': db_ok,
        'schema_ready': schema_ok,
        'db_engine': CONFIG.engine,
        'db_target': redacted_db_target(),
        'telegram_transport': telegram_transport_value,
        'telegram_webhook_enabled': telegram_webhook_enabled,
        'messenger_webhook_enabled': messenger_webhook_enabled,
        'webhook_runtime_enabled': webhook_runtime_enabled,
        'app_env': (os.getenv('APP_ENV', 'dev') or 'dev').strip().lower(),
        **_storage_health_fields(),
        **scheduler,
    }

    errors: list[str] = []
    if db_error is not None:
        errors.append(db_error)
    if schema_error is not None:
        errors.append(schema_error)
    if errors:
        details['error'] = ';'.join(errors)
        return details, 500
    return details, 200


async def _health(request: web.Request) -> web.Response:
    payload, status = build_health_payload()
    return web.json_response(payload, status=status)


async def start_health_runtime() -> HealthRuntime | None:
    enabled = (getattr(settings, 'HEALTHCHECK_ENABLED', True) or False)
    if not enabled:
        return None

    app = web.Application()
    app.router.add_get('/health', _health)
    app.router.add_get('/healthz', _health)

    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(
        runner,
        host=(settings.HEALTHCHECK_HOST or '127.0.0.1').strip(),
        port=int(getattr(settings, 'HEALTHCHECK_PORT', 8082) or 8082),
    )
    await site.start()
    log.info(
        'Health runtime started on %s:%s',
        settings.HEALTHCHECK_HOST,
        settings.HEALTHCHECK_PORT,
    )
    return HealthRuntime(runner=runner, site=site)
