from __future__ import annotations

from config.settings import settings


_POLLING_ALIASES = {'polling', 'telegram', 'longpoll', 'long-polling'}
_WEBHOOK_ALIASES = {'webhook'}


def telegram_transport() -> str:
    """Return canonical Telegram transport.

    Rules:
    - explicit webhook wins;
    - legacy TELEGRAM_TRANSPORT aliases map to polling by default;
    - legacy TELEGRAM_WEBHOOK_ENABLED=True upgrades polling-style aliases to webhook;
    - unknown values fail closed to webhook only when legacy flag is enabled, otherwise polling.
    """
    raw_transport = (getattr(settings, 'TELEGRAM_TRANSPORT', 'polling') or 'polling').strip().lower()
    webhook_enabled = bool(getattr(settings, 'TELEGRAM_WEBHOOK_ENABLED', False) or False)

    if raw_transport in _WEBHOOK_ALIASES:
        return 'webhook'
    if raw_transport in _POLLING_ALIASES:
        return 'webhook' if webhook_enabled else 'polling'
    return 'webhook' if webhook_enabled else 'polling'


__all__ = ['telegram_transport']
