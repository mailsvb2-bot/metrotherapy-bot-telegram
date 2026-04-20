from __future__ import annotations


import time
from dataclasses import dataclass


@dataclass
class _Entry:
    value: object
    expires_at: float


class Cache:
    """Tiny in-memory cache with TTL.

    No external deps, deterministic, good enough for weather/quotes.
    """

    def __init__(self):
        self._d: dict[str, _Entry] = {}

    def get(self, key: str):
        e = self._d.get(key)
        if not e:
            return None
        if e.expires_at and e.expires_at < time.time():
            self._d.pop(key, None)
            return None
        return e.value

    def set(self, key: str, value, ttl: int = 0):
        exp = time.time() + int(ttl) if ttl and ttl > 0 else 0
        self._d[key] = _Entry(value=value, expires_at=exp)


cache = Cache()
