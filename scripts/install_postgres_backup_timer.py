from __future__ import annotations

import os
import shutil
# Reviewed: operator installer invokes systemctl with fixed service/timer names and no shell.
import subprocess  # nosec B404
from pathlib import Path

SERVICE = "/etc/systemd/system/metrotherapy-postgres-backup.service"
TIMER = "/etc/systemd/system/metrotherapy-postgres-backup.timer"
ROOT = Path(os.getenv("METRO_ROOT", "/root/metrotherapy"))
PYTHON = ROOT / ".venv/bin/python"
ENV_FILE = Path(os.getenv("METROTHERAPY_ENV_FILE", "/etc/metrotherapy/metrotherapy.env"))
LEGACY_CRON = Path(os.getenv("METRO_POSTGRES_LEGACY_CRON", "/etc/cron.d/metrotherapy_pg_backup"))


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
ExecStart={PYTHON} scripts/postgres_backup.py --env-file {ENV_FILE}
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
    # Reviewed: fixed systemctl maintenance commands for the known timer unit, no shell.
    subprocess.run([systemctl, "daemon-reload"], check=True)  # nosec B603
    subprocess.run([systemctl, "enable", "--now", "metrotherapy-postgres-backup.timer"], check=True)  # nosec B603
    subprocess.run([systemctl, "start", "metrotherapy-postgres-backup.service"], check=True)  # nosec B603
    LEGACY_CRON.unlink(missing_ok=True)
    print("POSTGRES_BACKUP_TIMER_INSTALLED metrotherapy-postgres-backup.timer legacy_cron=removed")


def main() -> int:
    install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
