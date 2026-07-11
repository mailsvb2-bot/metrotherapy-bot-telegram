import asyncio
import logging
import os


def _normalize_telegram_token_env() -> None:
    """Accept the deployment alias used on servers: TELEGRAM_BOT_TOKEN.

    The application settings use BOT_TOKEN as the canonical name. Older server
    snippets and manual webhook commands often export TELEGRAM_BOT_TOKEN instead.
    Normalizing before importing app.py prevents a silent split where Telegram
    webhook setup uses one name while the bot process expects another.
    """
    if not (os.getenv("BOT_TOKEN") or "").strip():
        legacy_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        if legacy_token:
            os.environ["BOT_TOKEN"] = legacy_token


_normalize_telegram_token_env()

# В PROD не пишем байткод, чтобы релиз оставался чистым и не плодил __pycache__.
if os.getenv("APP_ENV", "dev").strip().lower() in {"prod", "production"}:
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

from app import create_application


log = logging.getLogger(__name__)


def _restart_limit() -> int:
    """Return crash-loop limit for APP_SELF_HEAL_RESTART.

    0 means intentionally unlimited. The default is finite so a repeated boot
    failure is visible to systemd/monitoring instead of being hidden forever.
    """
    raw = (os.getenv("APP_SELF_HEAL_MAX_RESTARTS") or "3").strip()
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 3


def _restart_backoff_sec() -> int:
    raw = (os.getenv("APP_SELF_HEAL_BACKOFF_SEC") or "2").strip()
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        log.warning("Bad APP_SELF_HEAL_BACKOFF_SEC=%r; using 2 seconds", raw)
        return 2


async def _run_with_restart() -> None:
    restart_enabled = (os.getenv("APP_SELF_HEAL_RESTART", "0") or "0").strip() in {"1", "true", "yes", "on"}
    backoff = _restart_backoff_sec()
    max_restarts = _restart_limit()
    crash_count = 0

    while True:
        try:
            await create_application()
            return
        except KeyboardInterrupt:
            return
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError):  # validator: allow-wide-except
            crash_count += 1
            log.exception("Application crashed")
            if not restart_enabled:
                raise
            if max_restarts and crash_count >= max_restarts:
                log.critical(
                    "Application crash-loop limit reached (%s/%s); refusing to hide repeated failure",
                    crash_count,
                    max_restarts,
                )
                raise
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    try:
        asyncio.run(_run_with_restart())
    except KeyboardInterrupt:
        pass
