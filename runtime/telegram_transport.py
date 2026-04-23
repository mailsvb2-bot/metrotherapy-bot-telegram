from __future__ import annotations

from config.settings import settings

_POLLING_ALIASES = {'polling', 'telegram', 'longpoll', 'long-polling'}
_WEBHOOK_ALIASES = {'webhook'}


def telegram_transport() -> str:
    raw_transport = (getattr(settings, 'TELEGRAM_TRANSPORT', 'polling') or 'polling').strip().lower()
    webhook_enabled = bool(getattr(settings, 'TELEGRAM_WEBHOOK_ENABLED', False) or False)

    if raw_transport in _WEBHOOK_ALIASES:
        return 'webhook'
    if raw_transport in _POLLING_ALIASES:
        return 'webhook' if webhook_enabled else 'polling'
    return 'webhook' if webhook_enabled else 'polling'
