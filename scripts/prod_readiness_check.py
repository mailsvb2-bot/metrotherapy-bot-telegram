from __future__ import annotations

"""Offline/live production-readiness checks.

This script deliberately does not call Telegram/YooKassa/VK/MAX. It validates
runtime contract before deploy or during post-deploy smoke: secrets must be
supplied through env, webhook/health ports must not collide, and required audio
folders must exist.

Release artifact checks are strict only when VALIDATOR_RELEASE_MODE=1 or
PROD_READINESS_RELEASE_MODE=1. A live server is expected to contain .env,
runtime DB files and logs; those are warnings in normal mode and errors in
release packaging mode.
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


def _release_mode() -> bool:
    return _truthy("VALIDATOR_RELEASE_MODE") or _truthy("PROD_READINESS_RELEASE_MODE")


def _require_postgres() -> bool:
    return _truthy("REQUIRE_POSTGRES") or _truthy("METRO_OBS_REQUIRE_POSTGRES")


def _require_env(name: str, errors: list[str], *, placeholder: bool = True) -> str:
    value = (os.getenv(name) or "").strip()
    if not value or (placeholder and _looks_placeholder(value)):
        errors.append(f"{name} is missing or placeholder")
    return value


def _validate_ai_runtime(prod: bool, errors: list[str], warnings: list[str]) -> None:
    enabled_raw = (os.getenv("AI_ENABLED") or "1").strip().lower()
    ai_enabled = enabled_raw not in {"0", "false", "no", "off"}
    provider = (os.getenv("AI_PROVIDER") or "").strip().lower()

    has_openai = bool((os.getenv("OPENAI_API_KEY") or "").strip())
    has_yandex = bool((os.getenv("YANDEX_API_KEY") or "").strip())
    has_gigachat = bool((os.getenv("GIGACHAT_CREDENTIALS") or "").strip())
    configured = has_openai or has_yandex or has_gigachat

    if not ai_enabled:
        return
    if not configured:
        warnings.append("AI_ENABLED is on, but no AI provider credentials are configured; admin AI surfaces will be disabled/degraded")
        return
    if provider and provider not in {"openai", "openai-compatible", "openai_compatible", "compatible", "yandex", "gigachat", "sber"}:
        errors.append(f"AI_PROVIDER has unsupported value: {provider!r}")
    if prod and provider == "yandex" and has_yandex and not (os.getenv("YANDEX_FOLDER_ID") or "").strip():
        warnings.append("YANDEX_API_KEY is set but YANDEX_FOLDER_ID is empty; default model resolution may be incomplete")


def _validate_database_runtime(prod: bool, errors: list[str], warnings: list[str]) -> None:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if database_url:
        if not database_url.startswith(("postgresql://", "postgres://")):
            warnings.append("DATABASE_URL is set but does not look like a Postgres URL")
        return
    message = "DATABASE_URL is empty; runtime will use SQLite"
    if prod and _require_postgres():
        errors.append(message + " but REQUIRE_POSTGRES=1")
    elif prod:
        warnings.append(message + " (acceptable for alpha/staging, not full production-grade)")


def _validate_messenger_runtime(prod: bool, messenger_webhook: bool, errors: list[str], warnings: list[str]) -> None:
    """Validate VK/MAX webhook runtime contract without making network calls."""
    public_base = (os.getenv("MESSENGER_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    max_token = (os.getenv("MAX_BOT_TOKEN") or "").strip()
    max_link_base = (os.getenv("MAX_BOT_LINK_BASE") or "").strip()
    vk_group_id = (os.getenv("VK_GROUP_ID") or "").strip()
    vk_group_token = (os.getenv("VK_GROUP_TOKEN") or "").strip()
    vk_confirmation = (os.getenv("VK_CONFIRMATION_TOKEN") or "").strip()

    max_configured = bool(max_token or max_link_base)
    vk_configured = bool(vk_group_id or vk_group_token or vk_confirmation)

    if not messenger_webhook:
        if prod and (max_configured or vk_configured):
            errors.append("MESSENGER_WEBHOOK_ENABLED=1 is required in prod when VK/MAX env is configured")
        return

    if not public_base:
        errors.append("MESSENGER_PUBLIC_BASE_URL is required for VK/MAX webhook runtime")
    elif prod and not public_base.startswith("https://"):
        errors.append("MESSENGER_PUBLIC_BASE_URL must start with https:// in prod")
    elif not (public_base.startswith("https://") or public_base.startswith("http://")):
        warnings.append("MESSENGER_PUBLIC_BASE_URL should be a full URL, for example https://your-domain.tld")

    if not (max_configured or vk_configured):
        errors.append("VK or MAX env must be configured when MESSENGER_WEBHOOK_ENABLED=1")
        return

    if max_configured:
        _require_env("MAX_BOT_TOKEN", errors)
        _require_env("MAX_BOT_LINK_BASE", errors, placeholder=False)
        if max_link_base and "{payload}" not in max_link_base:
            warnings.append("MAX_BOT_LINK_BASE has no {payload}; fallback ?start=... links may be less reliable")

    if vk_configured:
        _require_env("VK_GROUP_ID", errors)
        _require_env("VK_GROUP_TOKEN", errors)
        _require_env("VK_CONFIRMATION_TOKEN", errors)
        if not (os.getenv("VK_SECRET") or "").strip():
            warnings.append("VK_SECRET is empty; VK webhook secret verification is not enforced")


def _is_runtime_scan_candidate(path: Path) -> bool:
    try:
        rel_parts = path.relative_to(ROOT).parts
    except ValueError:
        return False
    return not any(part in SKIP_DIRS for part in rel_parts)


def _collect_release_artifacts() -> list[str]:
    forbidden: list[str] = []
    secret_patterns = (
        re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{25,}\b"),
        re.compile(r"live_[A-Za-z0-9_-]{16,}"),
    )
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel_parts = p.relative_to(ROOT).parts
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in rel_parts):
            continue
        if p.suffix.lower() in {".opus", ".ogg", ".mp3", ".wav", ".m4a", ".png", ".jpg", ".jpeg", ".zip", ".so"}:
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

    forbidden.extend(
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("__pycache__")
        if p.is_dir() and _is_runtime_scan_candidate(p)
    )
    forbidden.extend(
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("*.pyc")
        if p.is_file() and _is_runtime_scan_candidate(p)
    )
    forbidden.extend(
        str(p.relative_to(ROOT))
        for p in [ROOT / ".pytest_cache", ROOT / "data.db", ROOT / "data" / "data.db"]
        if p.exists()
    )
    forbidden.extend(
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("*.db-wal")
        if p.is_file() and _is_runtime_scan_candidate(p)
    )
    forbidden.extend(
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("*.db-shm")
        if p.is_file() and _is_runtime_scan_candidate(p)
    )
    forbidden.extend(
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("*.log")
        if p.is_file() and _is_runtime_scan_candidate(p)
    )
    return sorted(set(forbidden))


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

    _validate_database_runtime(prod, errors, warnings)
    _validate_ai_runtime(prod, errors, warnings)

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

    _validate_messenger_runtime(prod, messenger_webhook, errors, warnings)

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

    forbidden = _collect_release_artifacts()
    if forbidden:
        message = "Forbidden release/runtime artifacts present: " + ", ".join(forbidden[:30])
        if _release_mode():
            errors.append(message)
        else:
            warnings.append(message + " (strict only in release mode)")

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