from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from core.paths import LOGS_DIR, ROOT


def _resolve_log_path(raw: str) -> Path:
    path = Path(raw or "logs/app.log")
    if not path.is_absolute():
        path = ROOT / path
    return path


def setup_logging() -> None:
    level_name = (os.getenv("LOG_LEVEL", "INFO") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_path = _resolve_log_path(os.getenv("LOG_PATH", str(LOGS_DIR / "app.log")) or str(LOGS_DIR / "app.log"))
    max_bytes = int(os.getenv("LOG_MAX_BYTES", str(10_000_000)) or 10_000_000)
    backup_count = int(os.getenv("LOG_BACKUP_COUNT", "5") or 5)

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    root = logging.getLogger()
    root.setLevel(level)

    if getattr(setup_logging, "_configured", False):
        return

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        rotating = RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        rotating.setLevel(level)
        rotating.setFormatter(formatter)
        root.addHandler(rotating)
    except OSError:
        root.warning("RotatingFileHandler init failed for %s", log_path)

    setup_logging._configured = True
