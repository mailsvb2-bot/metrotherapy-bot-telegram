from __future__ import annotations

import os

from config.settings import settings

_POLLING_ALIASES = {'polling', 'telegram', 'longpoll', 'long-polling'}
_WEBHOOK_ALIASES = {'webhook'}


def _truthy(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on', 'webhook'}
    return bool(value)


def telegram_transport() -> str:
    """Resolve the canonical Telegram ingress transport from live environment.

    This resolver intentionally reads ``os.environ`` first instead of relying
    only on the already-instantiated settings object. Deployment tests and
    systemd overrides mutate env at process start; tests use monkeypatch. A
    stale settings snapshot here causes exactly the dangerous split-brain bug
    where health/webhook code thinks webhook is enabled while the app starts
    polling.
    """
    raw_transport = (
        os.getenv('TELEGRAM_TRANSPORT')
        or getattr(settings, 'TELEGRAM_TRANSPORT', 'polling')
        or 'polling'
    ).strip().lower()
    webhook_enabled = _truthy(
        os.getenv('TELEGRAM_WEBHOOK_ENABLED')
        if os.getenv('TELEGRAM_WEBHOOK_ENABLED') is not None
        else getattr(settings, 'TELEGRAM_WEBHOOK_ENABLED', False)
    )

    if raw_transport in _WEBHOOK_ALIASES:
        return 'webhook'
    if raw_transport in _POLLING_ALIASES:
        return 'webhook' if webhook_enabled else 'polling'
    return 'webhook' if webhook_enabled else 'polling'
