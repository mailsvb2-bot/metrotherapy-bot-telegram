from __future__ import annotations

import os
from pathlib import Path

class StartupCheckError(RuntimeError):
    pass


def _truthy_env(name: str, default: str = "0") -> bool:
    return (os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on", "webhook"}


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name, str(default)) or str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise StartupCheckError(f"Invalid integer env {name}={raw!r}") from exc


def _env_any(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _prod_ingress_checks() -> None:
    app_env = (os.getenv("APP_ENV", "dev") or "dev").strip().lower()
    telegram_transport = (os.getenv("TELEGRAM_TRANSPORT", os.getenv("RUN_MODE", "polling")) or "polling").strip().lower()
    telegram_webhook = telegram_transport == "webhook" or _truthy_env("TELEGRAM_WEBHOOK_ENABLED")
    messenger_webhook = _truthy_env("MESSENGER_WEBHOOK_ENABLED")
    any_webhook = telegram_webhook or messenger_webhook

    if app_env in {"prod", "production"}:
        if not (os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID") or "").strip():
            raise StartupCheckError("ADMIN_IDS or ADMIN_ID is required in prod")
        if not _truthy_env("HEALTHCHECK_ENABLED", "1"):
            raise StartupCheckError("HEALTHCHECK_ENABLED must be 1 in prod; readiness is part of the deployment contract")
        if telegram_webhook:
            public_base = (os.getenv("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip()
            if not public_base:
                raise StartupCheckError("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL is required when Telegram webhook transport is enabled")
            if not public_base.startswith("https://"):
                raise StartupCheckError("TELEGRAM_WEBHOOK_PUBLIC_BASE_URL must be https://... in prod")
            if not (os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN") or "").strip():
                raise StartupCheckError("TELEGRAM_WEBHOOK_SECRET_TOKEN is required in prod webhook mode")
            prefix = (os.getenv("TELEGRAM_WEBHOOK_PREFIX") or "/telegram-webhook").strip()
            if not prefix.startswith("/"):
                raise StartupCheckError("TELEGRAM_WEBHOOK_PREFIX must start with /")

    if any_webhook and _truthy_env("HEALTHCHECK_ENABLED", "1"):
        telegram_host = (os.getenv("TELEGRAM_WEBHOOK_HOST") or os.getenv("WEBHOOK_HOST") or "127.0.0.1").strip()
        telegram_port = _int_env("TELEGRAM_WEBHOOK_PORT", _int_env("WEBHOOK_PORT", 8081))
        messenger_host = (os.getenv("MESSENGER_WEBHOOK_HOST") or os.getenv("WEBHOOK_HOST") or "127.0.0.1").strip()
        messenger_port = _int_env("MESSENGER_WEBHOOK_PORT", _int_env("WEBHOOK_PORT", 8081))
        health_host = (os.getenv("HEALTHCHECK_HOST", "127.0.0.1") or "127.0.0.1").strip()
        health_port = _int_env("HEALTHCHECK_PORT", 8082)
        webhook_bindings = []
        if telegram_webhook:
            webhook_bindings.append((telegram_host, telegram_port, "telegram webhook"))
        if messenger_webhook:
            webhook_bindings.append((messenger_host, messenger_port, "messenger webhook"))
        for host, port, label in webhook_bindings:
            same_host = host == health_host or "0.0.0.0" in {host, health_host}
            if same_host and port == health_port:
                raise StartupCheckError(
                    f"Port collision: {label} and healthcheck both bind {host}:{port}. "
                    "Use separate ports, usually webhook=8081 and health=8082."
                )


def run_startup_checks(project_root: Path) -> None:
    """Fail-fast проверки целостности проекта.

    Цель: не стартовать «тихо криво», если нет критичных файлов/папок.
    Runtime-папки создаём сами: отсутствие data/logs/audio подкаталогов не должно
    превращать публичный вход `/start` в недоступный бот после чистого деплоя.
    """
    root = project_root.resolve()

    data_dir = root / "data"
    logs_dir = root / "logs"
    # Keep runtime directories present for both engines: SQLite needs the DB path,
    # Postgres still benefits from a stable runtime state/logs surface.
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Audio folders are runtime content mount points. Create them on clean deploys;
    # actual missing tracks must be handled by the audio flow, not by blocking /start.
    audio_dir = root / "audio"
    demo_dir = audio_dir / "demo"
    full_dir = audio_dir / "full"
    demo_dir.mkdir(parents=True, exist_ok=True)
    full_dir.mkdir(parents=True, exist_ok=True)

    # Critical modules introduced for stability
    critical_files = [
        root / "services" / "idempotency_keys.py",
        root / "core" / "task_manager.py",
        root / "services" / "db_writer.py",
    ]
    for p in critical_files:
        if not p.exists():
            raise StartupCheckError(f"Missing required file: {p}")

    _prod_ingress_checks()

    # Token sanity (do not print token). Support TELEGRAM_BOT_TOKEN for server snippets.
    if not _env_any("BOT_TOKEN", "TELEGRAM_BOT_TOKEN"):
        raise StartupCheckError("BOT_TOKEN is empty. Set BOT_TOKEN or TELEGRAM_BOT_TOKEN (see .env.example)")
