from __future__ import annotations

import asyncio
import os
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError


class ResilientBot(Bot):
    """Centralized Bot API retry policy for transient network failures.

    This keeps retry logic out of handlers and callback paths. It is especially
    important in webhook mode where a short Telegram/API hiccup should not turn
    into a broken UX for `/start` or callback buttons.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._request_timeout = float(os.getenv('TELEGRAM_REQUEST_TIMEOUT_SEC', '20') or '20')
        self._network_retries = max(0, int(os.getenv('TELEGRAM_NETWORK_RETRIES', '2') or '2'))
        self._network_retry_delay = float(os.getenv('TELEGRAM_NETWORK_RETRY_DELAY_SEC', '0.75') or '0.75')

    async def __call__(self, method: Any, request_timeout: Any = None) -> Any:
        timeout = request_timeout if request_timeout is not None else self._request_timeout
        last_exc: Exception | None = None
        for attempt in range(self._network_retries + 1):
            try:
                return await super().__call__(method, request_timeout=timeout)
            except (TelegramNetworkError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt >= self._network_retries:
                    raise
                await asyncio.sleep(self._network_retry_delay * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        raise RuntimeError('Unreachable resilient bot state')


def build_bot(token: str) -> ResilientBot:
    return ResilientBot(token=token)
