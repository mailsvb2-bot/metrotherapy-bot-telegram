from __future__ import annotations

import os
import re
from typing import Any

from services.visual_creative_gateway import gateway_snapshot

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_COUNTRY_RE = re.compile(r"[A-Za-z]{2}")


def _env(name: str) -> str:
    return str(os.getenv(name, "") or "").strip()


def _enabled_flag() -> tuple[bool, bool, str]:
    """Return (enabled, valid, mode) while preserving pre-flag deployments.

    Before VISUAL_CREATIVE_ENABLED existed, supplying gateway configuration was
    the activation mechanism. An unset flag therefore stays backward compatible:
    any gateway URL/token opts the capability in. New deployments should set the
    flag explicitly.
    """

    raw = _env("VISUAL_CREATIVE_ENABLED").lower()
    if not raw:
        implicit = bool(_env("VISUAL_GATEWAY_URL") or _env("VISUAL_GATEWAY_TOKEN"))
        return implicit, True, "implicit" if implicit else "disabled"
    if raw in _TRUE_VALUES:
        return True, True, "enabled"
    if raw in _FALSE_VALUES:
        return False, True, "disabled"
    return False, False, "invalid"


def _studio_enabled_flag() -> tuple[bool, bool, str]:
    """Keep render-pack Studio opt-in so base gateway deployments stay compatible."""

    raw = _env("VISUAL_CREATIVE_STUDIO_ENABLED").lower()
    if not raw:
        return False, True, "disabled"
    if raw in _TRUE_VALUES:
        return True, True, "enabled"
    if raw in _FALSE_VALUES:
        return False, True, "disabled"
    return False, False, "invalid"


def visual_creative_enabled() -> bool:
    enabled, valid, _mode = _enabled_flag()
    return bool(enabled and valid)


def visual_creative_studio_enabled() -> bool:
    enabled, valid, _mode = _studio_enabled_flag()
    return bool(enabled and valid and visual_creative_enabled())


def visual_creative_country_code() -> str:
    country = _env("VISUAL_DEPLOYMENT_COUNTRY")
    return country.upper() if _COUNTRY_RE.fullmatch(country) else ""


def visual_creative_configuration_snapshot(*, app_env: str | None = None) -> dict[str, Any]:
    enabled, flag_valid, mode = _enabled_flag()
    studio_enabled, studio_flag_valid, studio_mode = _studio_enabled_flag()
    gateway = gateway_snapshot()
    country = visual_creative_country_code()
    environment = str(app_env if app_env is not None else _env("APP_ENV") or "dev").strip().lower()
    production = environment in {"prod", "production"}

    errors: list[str] = []
    if not flag_valid:
        errors.append("invalid_enabled_flag")
    if not studio_flag_valid:
        errors.append("invalid_studio_enabled_flag")
    if studio_enabled and not (enabled and flag_valid):
        errors.append("studio_requires_visual_creative")
    if enabled:
        if not bool(gateway.get("configured")):
            errors.append("gateway_url")
        if not bool(gateway.get("token_configured")):
            errors.append("gateway_token")
        if not country:
            errors.append("deployment_country")
        if production and not bool(gateway.get("secure_transport")):
            errors.append("secure_transport")

    ready = bool(flag_valid and studio_flag_valid and not errors)
    return {
        "visual_creative_enabled": bool(enabled and flag_valid),
        "visual_creative_activation_mode": mode,
        "visual_creative_studio_enabled": bool(
            studio_enabled and studio_flag_valid and enabled and flag_valid
        ),
        "visual_creative_studio_activation_mode": studio_mode,
        "visual_creative_ready": ready,
        "visual_creative_gateway_configured": bool(gateway.get("configured")),
        "visual_creative_gateway_secure_transport": bool(gateway.get("secure_transport")),
        "visual_creative_gateway_token_configured": bool(gateway.get("token_configured")),
        "visual_creative_country_configured": bool(country),
        "visual_creative_configuration_errors": errors,
    }


__all__ = [
    "visual_creative_configuration_snapshot",
    "visual_creative_country_code",
    "visual_creative_enabled",
    "visual_creative_studio_enabled",
]
