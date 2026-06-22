from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_BACKUP_DIR = Path(os.getenv("METRO_POSTGRES_BACKUP_DIR", "/var/backups/metrotherapy/postgres"))
SUPPORTED_SUFFIXES = (".dump", ".sql", ".sql.gz")
DEFAULT_MAX_BACKUP_AGE_HOURS = float(os.getenv("METRO_POSTGRES_BACKUP_MAX_AGE_HOURS", "72") or "72")


@dataclass(frozen=True)
class DisasterRecoveryStatus:
    backup_dir: str
    latest_backup: str | None
    latest_backup_size_bytes: int
    latest_backup_mtime_utc: str | None
    latest_backup_sha256: str | None
    backup_count: int
    restore_target_configured: bool
    status: str
    reason: str
    latest_backup_age_seconds: int | None = None
    max_backup_age_hours: float = DEFAULT_MAX_BACKUP_AGE_HOURS
    latest_backup_fresh: bool | None = None

    @property
    def marker(self) -> str:
        return {"GREEN": "✅", "YELLOW": "⚠️", "RED": "🛑"}.get(self.status, "⚠️")

    @property
    def ok(self) -> bool:
        return self.status in {"GREEN", "YELLOW"} and bool(self.latest_backup)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "backup_dir": self.backup_dir,
            "latest_backup": self.latest_backup,
            "latest_backup_size_bytes": self.latest_backup_size_bytes,
            "latest_backup_mtime_utc": self.latest_backup_mtime_utc,
            "latest_backup_age_seconds": self.latest_backup_age_seconds,
            "max_backup_age_hours": self.max_backup_age_hours,
            "latest_backup_fresh": self.latest_backup_fresh,
            "latest_backup_sha256": self.latest_backup_sha256,
            "backup_count": self.backup_count,
            "restore_target_configured": self.restore_target_configured,
            "status": self.status,
            "reason": self.reason,
        }


def _supported(path: Path) -> bool:
    name = path.name.lower()
    return path.is_file() and any(name.endswith(suffix) for suffix in SUPPORTED_SUFFIXES)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _freshness(*, mtime_utc: datetime, max_age_hours: float) -> tuple[int, bool]:
    age_seconds = max(int((datetime.now(tz=UTC) - mtime_utc).total_seconds()), 0)
    if float(max_age_hours) <= 0:
        return age_seconds, True
    return age_seconds, age_seconds <= int(float(max_age_hours) * 3600)


def disaster_recovery_status(
    *,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    include_hash: bool = False,
    max_backup_age_hours: float = DEFAULT_MAX_BACKUP_AGE_HOURS,
) -> DisasterRecoveryStatus:
    target_configured = bool((os.getenv("METRO_RESTORE_DRILL_DATABASE_URL") or os.getenv("RESTORE_DATABASE_URL") or "").strip())
    if not backup_dir.exists():
        return DisasterRecoveryStatus(str(backup_dir), None, 0, None, None, 0, target_configured, "RED", "backup_dir_missing")
    backups = sorted((p for p in backup_dir.iterdir() if _supported(p)), key=lambda p: p.stat().st_mtime, reverse=True)
    if not backups:
        return DisasterRecoveryStatus(str(backup_dir), None, 0, None, None, 0, target_configured, "RED", "backup_missing")

    latest = backups[0]
    stat = latest.stat()
    latest_mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    latest_age_seconds, latest_fresh = _freshness(mtime_utc=latest_mtime, max_age_hours=float(max_backup_age_hours))

    if not latest_fresh:
        status = "RED"
        reason = f"backup_stale_gt_{float(max_backup_age_hours):g}h"
    elif target_configured:
        status = "GREEN"
        reason = "restore_target_configured"
    else:
        status = "YELLOW"
        reason = "backup_exists_restore_target_missing"

    return DisasterRecoveryStatus(
        backup_dir=str(backup_dir),
        latest_backup=str(latest),
        latest_backup_size_bytes=int(stat.st_size),
        latest_backup_mtime_utc=latest_mtime.isoformat(),
        latest_backup_sha256=_hash(latest) if include_hash else None,
        backup_count=len(backups),
        restore_target_configured=target_configured,
        status=status,
        reason=reason,
        latest_backup_age_seconds=latest_age_seconds,
        max_backup_age_hours=float(max_backup_age_hours),
        latest_backup_fresh=bool(latest_fresh),
    )


def format_disaster_recovery_status_for_admin() -> str:
    status = disaster_recovery_status(include_hash=False)
    lines = [
        "🧯 Disaster recovery / backup proof",
        "",
        f"Статус: {status.marker} {status.status}",
        f"Backup dir: {status.backup_dir}",
        f"Backup count: {status.backup_count}",
        f"Latest backup: {status.latest_backup or '-'}",
        f"Latest size: {status.latest_backup_size_bytes}",
        f"Latest mtime UTC: {status.latest_backup_mtime_utc or '-'}",
        f"Latest age seconds: {status.latest_backup_age_seconds if status.latest_backup_age_seconds is not None else '-'}",
        f"Max backup age hours: {status.max_backup_age_hours:g}",
        f"Latest backup fresh: {status.latest_backup_fresh}",
        f"Restore target configured: {status.restore_target_configured}",
        f"Reason: {status.reason}",
    ]
    if status.status == "GREEN":
        lines.append("\nИтог: backup свежий, backup найден, drill target настроен; можно запускать restore drill как gate.")
    elif status.status == "YELLOW":
        lines.append("\nИтог: backup найден, но drill target не настроен. Это не доказывает восстановление.")
    else:
        lines.append("\nИтог: backup proof не закрыт. Релизная зрелость ниже production-grade.")
    return "\n".join(lines)


__all__ = ["DisasterRecoveryStatus", "disaster_recovery_status", "format_disaster_recovery_status_for_admin"]
