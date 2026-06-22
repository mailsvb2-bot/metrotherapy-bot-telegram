from __future__ import annotations

import os

from config.settings import settings


def payment_public_base_url() -> str:
    """Return the canonical public base URL for package checkout links.

    Telegram, VK and MAX payment surfaces must use one resolver so package
    links cannot drift between providers.
    """
    return (
        os.getenv("MESSENGER_PUBLIC_BASE_URL", "").strip()
        or os.getenv("PAYMENT_PUBLIC_BASE_URL", "").strip()
        or os.getenv("PUBLIC_BASE_URL", "").strip()
        or str(getattr(settings, "MESSENGER_PUBLIC_BASE_URL", "") or "").strip()
        or str(getattr(settings, "TELEGRAM_WEBHOOK_PUBLIC_BASE_URL", "") or "").strip()
    ).rstrip("/")
