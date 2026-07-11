from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from config.settings import settings
from runtime.ingress_flags import max_webhook_enabled, payment_http_enabled, vk_webhook_enabled


@dataclass(frozen=True)
class MessengerPreflightStatus:
    channel: str
    ok: bool
    missing: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] | None = None


def _value(name: str, default: Any = "") -> Any:
    return getattr(settings, name, default)


def _env_or_setting(name: str, default: Any = "") -> Any:
    raw = os.getenv(name)
    if raw is not None:
        return raw
    return _value(name, default)


def _app_env() -> str:
    return (os.getenv("APP_ENV") or getattr(settings, "APP_ENV", "") or "dev").strip().lower()


def _deployed_env() -> bool:
    return _app_env() in {"prod", "production", "stage", "staging"}


def _public_webhook_url(path: str) -> str:
    base = str(_value("MESSENGER_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}{path}"


def _missing(*names: str) -> tuple[str, ...]:
    out: list[str] = []
    for name in names:
        value = _env_or_setting(name, "")
        if isinstance(value, bool):
            if not value:
                out.append(name)
            continue
        if not str(value or "").strip():
            out.append(name)
    return tuple(out)


def _https_warning(name: str, value: str, warnings: list[str]) -> None:
    clean = (value or "").strip()
    if clean and not clean.startswith("https://"):
        warnings.append(f"{name} should start with https:// in deployed environments")


def check_telegram_preflight() -> MessengerPreflightStatus:
    missing: list[str] = []
    warnings: list[str] = []
    if not str(_value("BOT_TOKEN", "") or "").strip():
        missing.append("BOT_TOKEN")
    transport = str(_value("TELEGRAM_TRANSPORT", "polling") or "polling").strip().lower()
    webhook_enabled = bool(_value("TELEGRAM_WEBHOOK_ENABLED", False)) or transport == "webhook"
    if webhook_enabled:
        for name in ("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL", "TELEGRAM_WEBHOOK_SECRET_TOKEN"):
            if not str(_value(name, "") or "").strip():
                missing.append(name)
        public_base = str(_value("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL", "") or "").strip()
        if public_base and not public_base.startswith("https://"):
            warnings.append("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL should start with https://")
    return MessengerPreflightStatus(
        channel="telegram",
        ok=not missing,
        missing=tuple(missing),
        warnings=tuple(warnings),
        details={
            "enabled": _deployed_env(),
            "transport": transport,
            "webhook_enabled": webhook_enabled,
        },
    )


def check_payment_preflight() -> MessengerPreflightStatus:
    enabled = payment_http_enabled()
    if not enabled:
        return MessengerPreflightStatus(channel="payment", ok=True, details={"enabled": False})

    missing = list(_missing("YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY"))
    signing_key = (
        (os.getenv("PAYMENT_CHECKOUT_SIGNING_KEY") or "").strip()
        or (os.getenv("CHECKOUT_SIGNING_KEY") or "").strip()
    )
    if not signing_key:
        missing.append("PAYMENT_CHECKOUT_SIGNING_KEY")

    public_base = str(
        os.getenv("PAYMENT_PUBLIC_BASE_URL")
        or os.getenv("MESSENGER_PUBLIC_BASE_URL")
        or os.getenv("PUBLIC_BASE_URL")
        or _value("MESSENGER_PUBLIC_BASE_URL", "")
        or ""
    ).strip()
    if not public_base:
        missing.append("PAYMENT_PUBLIC_BASE_URL")

    warnings: list[str] = []
    if _deployed_env():
        _https_warning("PAYMENT_PUBLIC_BASE_URL", public_base, warnings)

    return MessengerPreflightStatus(
        channel="payment",
        ok=not missing,
        missing=tuple(sorted(set(missing))),
        warnings=tuple(warnings),
        details={"enabled": True, "checkout_url": f"{public_base.rstrip('/')}/pay/yookassa" if public_base else ""},
    )


def check_vk_preflight() -> MessengerPreflightStatus:
    enabled = vk_webhook_enabled()
    if not enabled:
        return MessengerPreflightStatus(channel="vk", ok=True, details={"enabled": False})

    required = ["VK_GROUP_TOKEN", "VK_CONFIRMATION_TOKEN", "VK_GROUP_ID", "MESSENGER_PUBLIC_BASE_URL"]
    if _deployed_env():
        required.append("VK_SECRET")
    missing = _missing(*required)
    warnings: list[str] = []
    if not str(_value("VK_SECRET", "") or "").strip():
        warnings.append("VK_SECRET is not configured; VK webhook secret verification is not enforced")
    if _deployed_env():
        _https_warning("MESSENGER_PUBLIC_BASE_URL", str(_value("MESSENGER_PUBLIC_BASE_URL", "") or ""), warnings)
    return MessengerPreflightStatus(
        channel="vk",
        ok=not missing,
        missing=missing,
        warnings=tuple(warnings),
        details={"enabled": True, "webhook_url": _public_webhook_url("/webhooks/vk")},
    )


def check_max_preflight() -> MessengerPreflightStatus:
    enabled = max_webhook_enabled()
    if not enabled:
        return MessengerPreflightStatus(channel="max", ok=True, details={"enabled": False})

    required = ["MAX_BOT_TOKEN", "MAX_BOT_LINK_BASE", "MESSENGER_PUBLIC_BASE_URL"]
    if _deployed_env():
        required.append("MAX_WEBHOOK_SECRET")
    missing = _missing(*required)
    warnings: list[str] = []
    if not str(_value("MAX_WEBHOOK_SECRET", "") or "").strip():
        warnings.append("MAX_WEBHOOK_SECRET is not configured; MAX webhook secret verification is not enforced")
    if _deployed_env():
        _https_warning("MESSENGER_PUBLIC_BASE_URL", str(_value("MESSENGER_PUBLIC_BASE_URL", "") or ""), warnings)
    api_base = str(os.getenv("MAX_API_BASE_URL") or _value("MAX_API_BASE_URL", "") or "").strip()
    if "botapi.max.ru" in api_base:
        warnings.append("MAX_API_BASE_URL uses legacy botapi.max.ru domain")
    return MessengerPreflightStatus(
        channel="max",
        ok=not missing,
        missing=missing,
        warnings=tuple(warnings),
        details={
            "enabled": True,
            "webhook_url": _public_webhook_url("/webhooks/max"),
            "api_base": api_base or "https://platform-api.max.ru",
        },
    )


def check_all_preflights() -> tuple[MessengerPreflightStatus, ...]:
    return (
        check_telegram_preflight(),
        check_payment_preflight(),
        check_max_preflight(),
        check_vk_preflight(),
    )
