from __future__ import annotations

"""Offline production-readiness checks.

This script deliberately does not call Telegram/YooKassa. It validates the local
runtime contract before a deploy or post-deploy smoke: secrets must be supplied
through env, webhook/health ports must not collide, required audio folders must
exist, and release artifacts must not be present.
"""

import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {'.git', '.venv', 'venv', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}

ROOT = Path(__file__).resolve().parents[1]


def _truthy(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on", "webhook"}


def _int(name: str, default: int, errors: list[str]) -> int:
    raw = (os.getenv(name, str(default)) or str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        errors.append(f"{name} must be integer, got {raw!r}")
        return default


def _looks_placeholder(value: str) -> bool:
    return value.strip().upper().startswith("PASTE_") or value.strip() in {"", "x", "y", "changeme", "secret"}


def run() -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    app_env = (os.getenv("APP_ENV", "dev") or "dev").strip().lower()
    prod = app_env in {"prod", "production"}
    bot_token = (os.getenv("BOT_TOKEN") or "").strip()
    provider_token = (os.getenv("PAY_PROVIDER_TOKEN") or "").strip()

    if prod:
        for name, value in {"BOT_TOKEN": bot_token, "PAY_PROVIDER_TOKEN": provider_token}.items():
            if _looks_placeholder(value):
                errors.append(f"{name} is missing or placeholder")
        if bot_token and not re.match(r"^\d{8,12}:[A-Za-z0-9_-]{25,}$", bot_token):
            warnings.append("BOT_TOKEN format does not look like a Telegram bot token")
        if _looks_placeholder((os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID") or "").strip()):
            errors.append("ADMIN_IDS or ADMIN_ID is required in prod")
        if not (os.getenv("YOOKASSA_SHOP_ID") or "").strip():
            warnings.append("YOOKASSA_SHOP_ID is empty; fiscal receipt integration is incomplete")
        if not (os.getenv("YOOKASSA_SECRET_KEY") or "").strip():
            warnings.append("YOOKASSA_SECRET_KEY is empty; live YooKassa reconciliation/refunds cannot be verified")

    telegram_transport = (os.getenv("TELEGRAM_TRANSPORT", os.getenv("RUN_MODE", "polling")) or "polling").strip().lower()
    telegram_webhook = telegram_transport == "webhook" or _truthy("TELEGRAM_WEBHOOK_ENABLED")
    messenger_webhook = _truthy("MESSENGER_WEBHOOK_ENABLED")

    if prod and not _truthy("HEALTHCHECK_ENABLED", "1"):
        errors.append("HEALTHCHECK_ENABLED must be 1 in prod")

    if telegram_webhook:
        public_base = (os.getenv("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip()
        if not public_base:
            errors.append("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL is required for Telegram webhook mode")
        elif prod and not public_base.startswith("https://"):
            errors.append("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL must start with https:// in prod")
        prefix = (os.getenv("TELEGRAM_WEBHOOK_PREFIX", "/telegram-webhook") or "/telegram-webhook").strip()
        if not prefix.startswith("/"):
            errors.append("TELEGRAM_WEBHOOK_PREFIX must start with /")
        if prod and not (os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN") or "").strip():
            errors.append("TELEGRAM_WEBHOOK_SECRET_TOKEN is required in prod webhook mode")

    if telegram_webhook or messenger_webhook:
        wh_host = (os.getenv("TELEGRAM_WEBHOOK_HOST") or os.getenv("WEBHOOK_HOST") or "127.0.0.1").strip()
        wh_port = _int("TELEGRAM_WEBHOOK_PORT", _int("WEBHOOK_PORT", 8081, errors), errors)
        health_host = (os.getenv("HEALTHCHECK_HOST", "127.0.0.1") or "127.0.0.1").strip()
        health_port = _int("HEALTHCHECK_PORT", 8082, errors)
        same_host = wh_host == health_host or "0.0.0.0" in {wh_host, health_host}
        if same_host and wh_port == health_port:
            errors.append(f"Webhook port and health port collide on {wh_host}:{wh_port}")

    required_paths = [ROOT / "audio" / "demo", ROOT / "audio" / "full", ROOT / "data"]
    for p in required_paths:
        if not p.exists():
            errors.append(f"Required path missing: {p.relative_to(ROOT)}")

    forbidden = []
    secret_patterns = (
        re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{25,}\b"),
        re.compile(r"live_[A-Za-z0-9_-]{16,}"),
    )
    for p in ROOT.rglob("*"):
        if not p.is_file() or any(part in {".git", "__pycache__", ".pytest_cache", "dist"} for part in p.parts):
            continue
        if p.suffix.lower() in {".opus", ".ogg", ".mp3", ".wav", ".m4a", ".png", ".jpg", ".jpeg", ".zip"}:
            continue
        if p.name.startswith(".env"):
            forbidden.append(str(p.relative_to(ROOT)))
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(rx.search(txt) for rx in secret_patterns):
            forbidden.append(f"embedded-secret:{p.relative_to(ROOT)}")

    forbidden.extend(str(p.relative_to(ROOT)) for p in ROOT.rglob("__pycache__") if p.is_dir())
    forbidden.extend(str(p.relative_to(ROOT)) for p in ROOT.rglob("*.pyc") if p.is_file())
    forbidden.extend(str(p.relative_to(ROOT)) for p in [ROOT / ".pytest_cache", ROOT / "data.db", ROOT / "data" / "data.db"] if p.exists())
    forbidden.extend(str(p.relative_to(ROOT)) for p in ROOT.rglob("*.db-wal") if p.is_file())
    forbidden.extend(str(p.relative_to(ROOT)) for p in ROOT.rglob("*.db-shm") if p.is_file())
    forbidden.extend(str(p.relative_to(ROOT)) for p in ROOT.rglob("*.log") if p.is_file())
    if forbidden:
        errors.append("Forbidden release/runtime artifacts present: " + ", ".join(sorted(set(forbidden))[:30]))

    return errors, warnings


def main() -> int:
    errors, warnings = run()
    for warning in warnings:
        print(f"WARN: {warning}")
    if errors:
        print("PROD READINESS: FAILED")
        for error in errors:
            print(f"ERROR: {error}")
        return 2
    print("PROD READINESS: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
