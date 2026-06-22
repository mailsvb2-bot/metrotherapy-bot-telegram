from __future__ import annotations

import os
import subprocess
from pathlib import Path

SERVICE = "/etc/systemd/system/metrotherapy-postgres-backup.service"
TIMER = "/etc/systemd/system/metrotherapy-postgres-backup.timer"
ROOT = Path(os.getenv("METRO_ROOT", "/root/metrotherapy"))
PYTHON = ROOT / ".venv/bin/python"
ENV_FILE = ROOT / ".env"


def _write(path: str, content: str) -> None:
    Path(path).write_text(content, encoding="utf-8")


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
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "metrotherapy-postgres-backup.timer"], check=True)
    print("POSTGRES_BACKUP_TIMER_INSTALLED metrotherapy-postgres-backup.timer")


def main() -> int:
    install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
