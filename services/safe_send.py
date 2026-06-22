import asyncio
import logging
from typing import Callable, Awaitable, Iterable

from aiogram.exceptions import TelegramNetworkError

try:
    # aiohttp is the underlying transport for aiogram
    from aiohttp import ClientConnectionError  # type: ignore
except ImportError:  # pragma: no cover
    ClientConnectionError = OSError  # fallback

log = logging.getLogger(__name__)


async def safe(
    coro_factory: Callable[[], Awaitable],
    delays: Iterable[float] = (0.05, 0.15),
):
    """Safely execute a telegram send operation with short retries for transient network errors.

    Default delays are intentionally small to avoid breaking UX/SLA.
    For long background retries, pass a custom delays tuple.
    """
    last_exc: Exception | None = None
    for d in (0.0, *tuple(delays)):
        try:
            if d:
                await asyncio.sleep(d)
            return await coro_factory()
        except (TelegramNetworkError, ClientConnectionError, OSError) as e:
            last_exc = e
            log.warning("network send error: %s", e)
    if last_exc:
        raise last_exc
    raise RuntimeError("safe(): unknown error")
