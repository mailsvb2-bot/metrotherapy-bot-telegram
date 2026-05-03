from __future__ import annotations

"""Offline preflight checks for messenger channel readiness."""

from dataclasses import dataclass, field
from typing import Any, Literal

from config.settings import settings

ChannelName = Literal["telegram", "max", "vk"]


@dataclass(frozen=True)
class ChannelPreflightStatus:
    channel: ChannelName
    ok: bool
    missing: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


def _present(value: object) -> bool:
    return bool(str(value or "").strip())


def _https(value: object) -> bool:
    return str(value or "").strip().startswith("https://")


def check_max_preflight() -> ChannelPreflightStatus:
    missing: list[str] = []
    warnings: list[str] = []
    api_base = str(getattr(settings, "MAX_API_BASE_URL", "") or "https://platform-api.max.ru").strip().rstrip("/")
    public_base = str(getattr(settings, "MESSENGER_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")

    if not _present(getattr(settings, "MAX_BOT_TOKEN", "")):
        missing.append("MAX_BOT_TOKEN")
    if not _present(getattr(settings, "MAX_BOT_LINK_BASE", "")):
        missing.append("MAX_BOT_LINK_BASE")
    if bool(getattr(settings, "MESSENGER_WEBHOOK_ENABLED", False)):
        if not _present(public_base):
            missing.append("MESSENGER_PUBLIC_BASE_URL")
        if not _present(getattr(settings, "MAX_WEBHOOK_SECRET", "")):
            missing.append("MAX_WEBHOOK_SECRET")
        if public_base and not _https(public_base):
            warnings.append("MESSENGER_PUBLIC_BASE_URL must start with https:// for MAX webhook")

    if "botapi.max.ru" in api_base:
        warnings.append("MAX_API_BASE_URL uses legacy botapi.max.ru")
    elif not api_base.startswith("https://platform-api.max.ru"):
        warnings.append("MAX_API_BASE_URL should start with https://platform-api.max.ru")

    webhook_url = f"{public_base}/webhooks/max" if public_base else ""
    return ChannelPreflightStatus(
        channel="max",
        ok=not missing and not warnings,
        missing=tuple(missing),
        warnings=tuple(warnings),
        details={"api_base_url": api_base, "webhook_url": webhook_url},
    )


def check_vk_preflight() -> ChannelPreflightStatus:
    missing: list[str] = []
    warnings: list[str] = []
    public_base = str(getattr(settings, "MESSENGER_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")

    for name in ("VK_GROUP_TOKEN", "VK_CONFIRMATION_TOKEN", "VK_GROUP_ID"):
        if not _present(getattr(settings, name, "")):
            missing.append(name)
    if bool(getattr(settings, "MESSENGER_WEBHOOK_ENABLED", False)):
        if not _present(public_base):
            missing.append("MESSENGER_PUBLIC_BASE_URL")
        elif not _https(public_base):
            warnings.append("MESSENGER_PUBLIC_BASE_URL should start with https:// for VK webhook")

    webhook_url = f"{public_base}/webhooks/vk" if public_base else ""
    return ChannelPreflightStatus(
        channel="vk",
        ok=not missing and not warnings,
        missing=tuple(missing),
        warnings=tuple(warnings),
        details={"webhook_url": webhook_url, "api_version": getattr(settings, "VK_API_VERSION", "5.199")},
    )


def check_telegram_preflight() -> ChannelPreflightStatus:
    missing: list[str] = []
    warnings: list[str] = []
    transport = str(getattr(settings, "TELEGRAM_TRANSPORT", "polling") or "polling").strip().lower()
    webhook_enabled = bool(getattr(settings, "TELEGRAM_WEBHOOK_ENABLED", False)) or transport == "webhook"
    public_base = str(getattr(settings, "TELEGRAM_WEBHOOK_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")

    if not _present(getattr(settings, "BOT_TOKEN", "")):
        missing.append("BOT_TOKEN")
    if webhook_enabled:
        if not _present(public_base):
            missing.append("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL")
        elif not _https(public_base):
            warnings.append("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL should start with https://")
        if not _present(getattr(settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "")):
            warnings.append("TELEGRAM_WEBHOOK_SECRET_TOKEN is recommended in webhook mode")

    return ChannelPreflightStatus(
        channel="telegram",
        ok=not missing and not warnings,
        missing=tuple(missing),
        warnings=tuple(warnings),
        details={"transport": transport, "webhook_public_base_url": public_base},
    )


def check_all_preflights() -> tuple[ChannelPreflightStatus, ...]:
    return (check_telegram_preflight(), check_max_preflight(), check_vk_preflight())
