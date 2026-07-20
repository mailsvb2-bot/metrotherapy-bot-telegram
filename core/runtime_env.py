from __future__ import annotations

import logging
import math
import os

log = logging.getLogger(__name__)


def env_int(
    name: str,
    default: int,
    *,
    fallback_name: str = "",
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = os.getenv(name)
    if raw in (None, "") and fallback_name:
        raw = os.getenv(fallback_name)
    raw = str(default) if raw in (None, "") else str(raw).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning("Invalid integer env %s; using default=%s", name, default)
        return int(default)
    if minimum is not None and value < minimum:
        log.warning("Out-of-range integer env %s; using default=%s", name, default)
        return int(default)
    if maximum is not None and value > maximum:
        log.warning("Out-of-range integer env %s; using default=%s", name, default)
        return int(default)
    return value


def env_float(
    name: str,
    default: float,
    *,
    fallback_name: str = "",
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = os.getenv(name)
    if raw in (None, "") and fallback_name:
        raw = os.getenv(fallback_name)
    raw = str(default) if raw in (None, "") else str(raw).strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.warning("Invalid float env %s; using default=%s", name, default)
        return float(default)
    if not math.isfinite(value):
        log.warning("Non-finite float env %s; using default=%s", name, default)
        return float(default)
    if minimum is not None and value < minimum:
        log.warning("Out-of-range float env %s; using default=%s", name, default)
        return float(default)
    if maximum is not None and value > maximum:
        log.warning("Out-of-range float env %s; using default=%s", name, default)
        return float(default)
    return value
