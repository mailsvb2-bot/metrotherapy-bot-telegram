from __future__ import annotations

import json
import os
import shutil
# Reviewed: release report only invokes local git for metadata, without shell.
import subprocess  # nosec B404
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.auto_audio_recovery import auto_audio_lock_summary
from services.disaster_recovery_status import disaster_recovery_status
from services.payments.reconciliation import payment_problem_summary
from services.probe_ledger import ProbeRun, get_recent_probe_runs
from services.scheduler import scheduler_health_snapshot
from services.storage_legacy_audit import storage_legacy_audit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HEALTH_URL = "http://127.0.0.1:8082/health"

REQUIRED_PROBES = {
    "payment_entitlement_reconciliation_probe": "Payment entitlement",
    "probe_scheduler_job_live": "Scheduler job",
    "auto_audio_dry_run_probe": "Auto-audio dry-run",
    "synthetic_user_journey_e2e_probe": "User journey E2E",
}


@dataclass(frozen=True)
class ReleaseProbeStatus:
    probe_type: str
    label: str
    status: str
    cleanup_status: str
    rows_touched: int
    run_id: str
    finished_at_utc: str | None
    error: str | None

    @property
    def is_green(self) -> bool:
        return self.status == "ok" and self.cleanup_status in {"clean", "dry_run"}


@dataclass(frozen=True)
class ReleaseControlSnapshot:
    status: str
    marker: str
    git_branch: str
    git_commit: str
    storage_status: str
    storage_legacy_sqlite_present: bool
    storage_repo_sqlite_present: bool
    storage_disallowed_sqlite_connects: int
    disaster_recovery_status: str
    disaster_recovery_reason: str
    disaster_backup_count: int
    disaster_latest_backup: str | None
    disaster_latest_backup_size_bytes: int
    disaster_restore_target_configured: bool
    scheduler_loop_running: bool
    scheduler_loop_error_count: int
    scheduler_loop_last_error: str
    scheduler_loop_last_tick_age_sec: int
    payment_problem_count: int
    stale_auto_audio_lock_count: int
    probe_statuses: list[ReleaseProbeStatus]
    recent_probe_runs: list[ProbeRun]
    stale_auto_audio_locks: list[dict[str, Any]]



def _optional_bin(name: str, *, env_name: str | None = None) -> str | None:
    raw = (os.getenv(env_name or "") or name).strip()
    return shutil.which(raw) if raw else None


def _git_value(*args: str) -> str:
    git = _optional_bin("git", env_name="GIT_BIN")
    if not git:
        return "unknown"
    try:
        # Reviewed: local git binary path is resolved by shutil.which and args are fixed call sites.
        proc = subprocess.run(  # nosec B603
            [git, *args],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            timeout=2,
            check=False,
        )
    except OSError:
        return "unknown"
    except subprocess.SubprocessError:
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    return (proc.stdout or "").strip() or "unknown"



def _latest_by_type(runs: list[ProbeRun]) -> dict[str, ProbeRun]:
    result: dict[str, ProbeRun] = {}
    for run in runs:
        if run.probe_type and run.probe_type not in result:
            result[run.probe_type] = run
    return result



def _probe_statuses(runs: list[ProbeRun]) -> list[ReleaseProbeStatus]:
    latest = _latest_by_type(runs)
    statuses: list[ReleaseProbeStatus] = []
    for probe_type, label in REQUIRED_PROBES.items():
        run = latest.get(probe_type)
        if run is None:
            statuses.append(
                ReleaseProbeStatus(
                    probe_type=probe_type,
                    label=label,
                    status="missing",
                    cleanup_status="",
                    rows_touched=0,
                    run_id="",
                    finished_at_utc=None,
                    error="missing_probe_run",
                )
            )
            continue
        statuses.append(
            ReleaseProbeStatus(
                probe_type=probe_type,
                label=label,
                status=str(run.status or ""),
                cleanup_status=str(run.cleanup_status or ""),
                rows_touched=int(run.rows_touched or 0),
                run_id=str(run.run_id or ""),
                finished_at_utc=run.finished_at_utc,
                error=run.error,
            )
        )
    return statuses



def _health_payload(url: str = DEFAULT_HEALTH_URL) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # nosec B310
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {}



def get_release_control_snapshot(*, limit: int = 25) -> ReleaseControlSnapshot:
    recent = get_recent_probe_runs(limit=limit)
    storage = storage_legacy_audit()
    recovery = disaster_recovery_status(include_hash=False)
    scheduler = scheduler_health_snapshot()
    payment_summary = payment_problem_summary()
    auto_audio = auto_audio_lock_summary()
    probe_statuses = _probe_statuses(recent)
    health = _health_payload()

    degraded_reasons: list[str] = []
    if storage.status != "GREEN":
        degraded_reasons.append(f"storage={storage.status}")
    if recovery.status != "GREEN":
        degraded_reasons.append(f"backup={recovery.status}")
    if not scheduler.loop_running:
        degraded_reasons.append("scheduler_loop_not_running")
    if int(scheduler.loop_error_count or 0) > 0:
        degraded_reasons.append(f"scheduler_errors={scheduler.loop_error_count}")
    if payment_summary.problem_count > 0:
        degraded_reasons.append(f"payment_problems={payment_summary.problem_count}")
    if auto_audio.stale_count > 0:
        degraded_reasons.append(f"stale_auto_audio_locks={auto_audio.stale_count}")
    if health.get("telegram_transport") != "polling":
        degraded_reasons.append(f"telegram_transport={health.get('telegram_transport')}")
    if health.get("telegram_webhook_enabled") is not False:
        degraded_reasons.append("telegram_webhook_enabled")
    missing_probes = [status.probe_type for status in probe_statuses if not status.is_green]
    if missing_probes:
        degraded_reasons.append("probes=" + ",".join(missing_probes))

    status = "GREEN" if not degraded_reasons else "YELLOW"
    return ReleaseControlSnapshot(
        status=status,
        marker="PRODUCTION_READY" if status == "GREEN" else "PRODUCTION_DEGRADED",
        git_branch=_git_value("rev-parse", "--abbrev-ref", "HEAD"),
        git_commit=_git_value("rev-parse", "--short", "HEAD"),
        storage_status=storage.status,
        storage_legacy_sqlite_present=storage.legacy_sqlite_present,
        storage_repo_sqlite_present=storage.repo_local_sqlite_present,
        storage_disallowed_sqlite_connects=len(storage.disallowed_direct_sqlite_connects),
        disaster_recovery_status=recovery.status,
        disaster_recovery_reason=recovery.reason,
        disaster_backup_count=recovery.backup_count,
        disaster_latest_backup=recovery.latest_backup,
        disaster_latest_backup_size_bytes=recovery.latest_backup_size_bytes,
        disaster_restore_target_configured=recovery.restore_target_configured,
        scheduler_loop_running=scheduler.loop_running,
        scheduler_loop_error_count=scheduler.loop_error_count,
        scheduler_loop_last_error=scheduler.loop_last_error,
        scheduler_loop_last_tick_age_sec=scheduler.loop_last_tick_age_sec,
        payment_problem_count=payment_summary.problem_count,
        stale_auto_audio_lock_count=auto_audio.stale_count,
        probe_statuses=probe_statuses,
        recent_probe_runs=recent,
        stale_auto_audio_locks=auto_audio.stale_locks,
    )


def format_release_control_report(*, limit: int = 25) -> str:
    snapshot = get_release_control_snapshot(limit=limit)
    lines = [
        f"Release control: {snapshot.status} ({snapshot.marker})",
        f"Git: {snapshot.git_branch}@{snapshot.git_commit}",
        f"Storage: {snapshot.storage_status} legacy_sqlite={snapshot.storage_legacy_sqlite_present} repo_sqlite={snapshot.storage_repo_sqlite_present}",
        f"Backups: {snapshot.disaster_recovery_status} count={snapshot.disaster_backup_count} latest={snapshot.disaster_latest_backup or '-'}",
        f"Scheduler: running={snapshot.scheduler_loop_running} errors={snapshot.scheduler_loop_error_count} last_tick_age={snapshot.scheduler_loop_last_tick_age_sec}s",
        f"Payments: problems={snapshot.payment_problem_count}",
        f"Auto-audio locks: stale={snapshot.stale_auto_audio_lock_count}",
        "Probes:",
    ]
    for status in snapshot.probe_statuses:
        icon = "✅" if status.is_green else "⚠️"
        lines.append(
            f"  {icon} {status.label}: {status.status}/{status.cleanup_status} rows={status.rows_touched} run={status.run_id or '-'}"
        )
    return "\n".join(lines)
