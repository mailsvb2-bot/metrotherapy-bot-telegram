from __future__ import annotations

import logging
import os
import sqlite3
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


def _webhook_configured() -> bool:
    try:
        messenger_enabled = bool(getattr(settings, 'MESSENGER_WEBHOOK_ENABLED', False) or False)
        telegram_enabled = telegram_transport() == 'webhook'
        return bool(messenger_enabled or telegram_enabled)
    except (AttributeError, RuntimeError):
        return False



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



def build_health_payload() -> tuple[dict[str, Any], int]:
    db_ok, db_error = _db_ready()
    schema_ok, schema_error = _schema_ready()
    scheduler = _scheduler_snapshot()
    details: dict[str, Any] = {
        'ok': bool(db_ok and schema_ok),
        'service': 'metrotherapy',
        'db_ready': db_ok,
        'schema_ready': schema_ok,
        'db_engine': CONFIG.engine,
        'db_target': redacted_db_target(),
        'db_path': str(DB_PATH),
        'db_exists': False,
        'root_exists': False,
        'messenger_webhook_enabled': _webhook_configured(),
        'app_env': (os.getenv('APP_ENV', 'dev') or 'dev').strip().lower(),
        **scheduler,
    }
    try:
        details['db_exists'] = Path(DB_PATH).exists()
        details['root_exists'] = ROOT.exists()
    except OSError:
        details['db_exists'] = False
        details['root_exists'] = False

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
