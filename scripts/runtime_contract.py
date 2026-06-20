from __future__ import annotations

"""Production runtime contract checks for Metrotherapy.

This script is intentionally offline: it does not call Telegram, providers, or
external services. It validates the server/process contract that must hold before
ads or live traffic are sent to the bot.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _truthy(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on", "webhook"}


def _value(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _first_value(*names: str) -> str:
    for name in names:
        value = _value(name)
        if value:
            return value
    return ""


def _payment_public_base_url() -> str:
    return _first_value("MESSENGER_PUBLIC_BASE_URL", "PAYMENT_PUBLIC_BASE_URL", "PUBLIC_BASE_URL").rstrip("/")


def _is_abs_outside_project(raw: str) -> bool:
    if not raw:
        return False
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            return False
        resolved = path.resolve()
        root = ROOT.resolve()
        return resolved != root and root not in resolved.parents
    except OSError:
        return False


def run() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    app_env = (_value("APP_ENV") or "dev").lower()
    prod = app_env in {"prod", "production"}

    transport = (_value("TELEGRAM_TRANSPORT") or _value("RUN_MODE") or "polling").lower()
    if transport != "polling":
        errors.append("TELEGRAM_TRANSPORT must remain polling for this deployment")
    if _truthy("TELEGRAM_WEBHOOK_ENABLED"):
        errors.append("TELEGRAM_WEBHOOK_ENABLED must be 0; Telegram must not be switched to webhook")

    if prod:
        for name in ("APP_ENV", "BOT_TOKEN", "ADMIN_IDS"):
            if not _value(name):
                errors.append(f"{name} is required in prod")

        # Canonical payment path is external YooKassa/package checkout. Legacy
        # Telegram invoice provider token must not be a production dependency.
        for name in ("YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY", "PAYMENT_CHECKOUT_SIGNING_KEY"):
            if not _value(name):
                errors.append(f"{name} is required in prod")
        if not _first_value("YOOKASSA_WEBHOOK_SECRET", "PAYMENT_WEBHOOK_SECRET", "WEBHOOK_SECRET"):
            errors.append("YOOKASSA_WEBHOOK_SECRET is required in prod")
        payment_base = _payment_public_base_url()
        if not payment_base:
            errors.append("PAYMENT_PUBLIC_BASE_URL or MESSENGER_PUBLIC_BASE_URL is required in prod")
        elif not payment_base.startswith("https://"):
            errors.append("payment public base URL must start with https:// in prod")

        db_path = _value("METRO_DB_PATH")
        log_path = _value("LOG_PATH")
        if not _is_abs_outside_project(db_path):
            errors.append("METRO_DB_PATH must be an absolute path outside the project tree in prod")
        if not _is_abs_outside_project(log_path):
            errors.append("LOG_PATH must be an absolute path outside the project tree in prod")
        if _truthy("HEALTHCHECK_ENABLED", "1") is False:
            errors.append("HEALTHCHECK_ENABLED must be 1 in prod")

    messenger_enabled = _truthy("MESSENGER_WEBHOOK_ENABLED")
    if messenger_enabled:
        if not _value("MESSENGER_PUBLIC_BASE_URL"):
            errors.append("MESSENGER_PUBLIC_BASE_URL is required when messenger webhook runtime is enabled")
        if _truthy("TELEGRAM_WEBHOOK_ENABLED"):
            errors.append("Messenger webhook runtime must not imply Telegram webhook mode")

        messenger_host = _value("MESSENGER_WEBHOOK_HOST") or _value("WEBHOOK_HOST") or "127.0.0.1"
        messenger_port = int(_value("MESSENGER_WEBHOOK_PORT") or _value("WEBHOOK_PORT") or "8081")
        health_host = _value("HEALTHCHECK_HOST") or "127.0.0.1"
        health_port = int(_value("HEALTHCHECK_PORT") or "8082")
        same_host = messenger_host == health_host or "0.0.0.0" in {messenger_host, health_host}
        if same_host and messenger_port == health_port:
            errors.append(f"Messenger webhook port and health port collide on {messenger_host}:{messenger_port}")

    if not messenger_enabled:
        warnings.append("MESSENGER_WEBHOOK_ENABLED is 0; MAX/VK/YooKassa web endpoints will not be served by this process")

    return errors, warnings


def main() -> int:
    errors, warnings = run()
    for warning in warnings:
        print(f"WARN: {warning}")
    if errors:
        print("RUNTIME CONTRACT: FAILED")
        for error in errors:
            print(f"ERROR: {error}")
        return 2
    print("RUNTIME CONTRACT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
