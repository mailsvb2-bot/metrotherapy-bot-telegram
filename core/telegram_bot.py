from __future__ import annotations

import asyncio
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError

from core.runtime_env import env_float, env_int


class ResilientBot(Bot):
    """Centralized Bot API retry policy for transient network failures."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._request_timeout = env_float(
            "TELEGRAM_REQUEST_TIMEOUT_SEC",
            20.0,
            minimum=1.0,
            maximum=120.0,
        )
        self._network_retries = env_int(
            "TELEGRAM_NETWORK_RETRIES",
            2,
            minimum=0,
            maximum=10,
        )
        self._network_retry_delay = env_float(
            "TELEGRAM_NETWORK_RETRY_DELAY_SEC",
            0.75,
            minimum=0.0,
            maximum=30.0,
        )

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
        raise RuntimeError("Unreachable resilient bot state")


def build_bot(token: str) -> ResilientBot:
    return ResilientBot(token=token)
