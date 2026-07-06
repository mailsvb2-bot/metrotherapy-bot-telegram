from __future__ import annotations

import os
import shutil
from pathlib import Path

from services.command_runner import run_command

SERVICE = "/etc/systemd/system/metrotherapy-postgres-backup.service"
TIMER = "/etc/systemd/system/metrotherapy-postgres-backup.timer"
ROOT = Path(os.getenv("METRO_ROOT", "/root/metrotherapy"))
PYTHON = ROOT / ".venv/bin/python"
ENV_FILE = ROOT / ".env"


def _write(path: str, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


def _required_bin(name: str, *, env_name: str | None = None) -> str:
    raw = (os.getenv(env_name or "") or name).strip()
    resolved = shutil.which(raw) if raw else None
    if resolved:
        return resolved
    raise SystemExit(f"required executable not found: {raw or name}")


def install() -> None:
    service = f"""[Unit]
Description=Metrotherapy Postgres backup and restore drill
After=network-online.target postgresql.service

[Service]
Type=oneshot
WorkingDirectory={ROOT}
EnvironmentFile={ENV_FILE}
ExecStart={PYTHON} scripts/postgres_backup.py
ExecStart={PYTHON} scripts/postgres_restore_drill.py --latest
"""
    timer = """[Unit]
Description=Run Metrotherapy Postgres backup and restore drill daily

[Timer]
OnCalendar=*-*-* 03:25:00
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
"""
    _write(SERVICE, service)
    _write(TIMER, timer)
    systemctl = _required_bin("systemctl", env_name="SYSTEMCTL_BIN")
    run_command([systemctl, "daemon-reload"], check=True)
    run_command([systemctl, "enable", "--now", "metrotherapy-postgres-backup.timer"], check=True)
    print("POSTGRES_BACKUP_TIMER_INSTALLED metrotherapy-postgres-backup.timer")


def main() -> int:
    install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
