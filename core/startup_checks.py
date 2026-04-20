from __future__ import annotations

import os
from pathlib import Path

class StartupCheckError(RuntimeError):
    pass


def run_startup_checks(project_root: Path) -> None:
    """Fail-fast проверки целостности проекта.

    Цель: не стартовать «тихо криво», если нет критичных файлов/папок.
    """
    root = project_root.resolve()

    data_dir = root / "data"
    logs_dir = root / "logs"
    # Keep runtime directories present for both engines: SQLite needs the DB path,
    # Postgres still benefits from a stable runtime state/logs surface.
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Audio folders
    audio_dir = root / "audio"
    if not audio_dir.exists():
        raise StartupCheckError(f"Missing required directory: {audio_dir}")
    demo_dir = audio_dir / "demo"
    if not demo_dir.exists():
        raise StartupCheckError(f"Missing required directory: {demo_dir}")
    full_dir = audio_dir / "full"
    if not full_dir.exists():
        raise StartupCheckError(f"Missing required directory: {full_dir}")

    # Critical modules introduced for stability
    critical_files = [
        root / "services" / "idempotency_keys.py",
        root / "core" / "task_manager.py",
        root / "services" / "db_writer.py",
    ]
    for p in critical_files:
        if not p.exists():
            raise StartupCheckError(f"Missing required file: {p}")

    # Token sanity (do not print token)
    if not (os.getenv("BOT_TOKEN") or "").strip():
        # app.py also checks; keep here to surface in one place
        raise StartupCheckError("BOT_TOKEN is empty. Put it into .env (see .env.example)")
