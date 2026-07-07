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


def _resolved_db_engine() -> str:
    raw = (os.getenv("METRO_DB_ENGINE") or "").strip().lower()
    if raw in {"postgres", "postgresql", "pg"}:
        return "postgres"
    if raw in {"sqlite", "sqlite3"}:
        return "sqlite"
    return "postgres" if (os.getenv("DATABASE_URL") or "").strip() else "sqlite"


def _prod_ingress_checks() -> None:
    app_env = (os.getenv("APP_ENV", "dev") or "dev").strip().lower()
    telegram_transport = (os.getenv("TELEGRAM_TRANSPORT", os.getenv("RUN_MODE", "polling")) or "polling").strip().lower()
    telegram_webhook = telegram_transport == "webhook" or _truthy_env("TELEGRAM_WEBHOOK_ENABLED")
    messenger_webhook = _truthy_env("MESSENGER_WEBHOOK_ENABLED")
    any_webhook = messenger_webhook

    if app_env in {"prod", "production"}:
        if not (os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID") or "").strip():
            raise StartupCheckError("ADMIN_IDS or ADMIN_ID is required in prod")
        if not _truthy_env("HEALTHCHECK_ENABLED", "1"):
            raise StartupCheckError("HEALTHCHECK_ENABLED must be 1 in prod; readiness is part of the deployment contract")
        if telegram_webhook:
            raise StartupCheckError(
                "Telegram production ingress is polling-only: set TELEGRAM_TRANSPORT=polling and TELEGRAM_WEBHOOK_ENABLED=0"
            )
        if _truthy_env("TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED"):
            raise StartupCheckError("TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED must be 0 in prod")
        if _truthy_env("ALLOW_INSECURE_TELEGRAM_WEBHOOK"):
            raise StartupCheckError("ALLOW_INSECURE_TELEGRAM_WEBHOOK is forbidden in prod")

        if _resolved_db_engine() != "postgres":
            raise StartupCheckError("METRO_DB_ENGINE must be postgres in prod")
        database_url = (os.getenv("DATABASE_URL") or "").strip()
        if not database_url:
            raise StartupCheckError("DATABASE_URL is required in prod")
        if not database_url.lower().startswith(("postgresql://", "postgres://")):
            raise StartupCheckError("DATABASE_URL must use postgres/postgresql scheme in prod")

    if any_webhook and _truthy_env("HEALTHCHECK_ENABLED", "1"):
        messenger_host = (os.getenv("MESSENGER_WEBHOOK_HOST") or os.getenv("WEBHOOK_HOST") or "127.0.0.1").strip()
        messenger_port = _int_env("MESSENGER_WEBHOOK_PORT", _int_env("WEBHOOK_PORT", 8081))
        health_host = (os.getenv("HEALTHCHECK_HOST", "127.0.0.1") or "127.0.0.1").strip()
        health_port = _int_env("HEALTHCHECK_PORT", 8082)
        same_host = messenger_host == health_host or "0.0.0.0" in {messenger_host, health_host}  # nosec B104 - sentinel comparison, not a bind
        if same_host and messenger_port == health_port:
            raise StartupCheckError(
                f"Port collision: messenger webhook and healthcheck both bind {messenger_host}:{messenger_port}. "
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
        raise StartupCheckError("BOT_TOKEN is empty. Set BOT_TOKEN or TELEGRAM_BOT_TOKEN (see deploy/metrotherapy.env.example)")
