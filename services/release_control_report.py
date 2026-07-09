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



def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


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
                    cleanup_status="missing",
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
    except (urllib.error.URLError, TimeoutError, OSError):
        return {}
    except json.JSONDecodeError:
        return {}


def _runtime_health_scheduler_snapshot() -> dict[str, Any]:
    return _health_payload()


def _payment_problem_count(summary: Any) -> int:
    if isinstance(summary, list):
        return len(summary)
    return int(_get(summary, "problem_count", 0) or 0)


def _auto_audio_stale_count(summary: Any) -> int:
    return int(_get(summary, "stale_count", _get(summary, "stale_lock_count", 0)) or 0)


def _auto_audio_stale_locks(summary: Any) -> list[dict[str, Any]]:
    locks = _get(summary, "stale_locks", _get(summary, "locks", [])) or []
    return list(locks)


def _scheduler_running(summary: Any) -> bool:
    return bool(_get(summary, "loop_running", _get(summary, "scheduler_loop_task_running", False)))


def _scheduler_error_count(summary: Any) -> int:
    return int(_get(summary, "loop_error_count", _get(summary, "scheduler_loop_error_count", 0)) or 0)


def _scheduler_last_error(summary: Any) -> str:
    return str(_get(summary, "loop_last_error", _get(summary, "scheduler_loop_last_error", "")) or "")


def _scheduler_last_tick_age_sec(summary: Any) -> int:
    return int(_get(summary, "loop_last_tick_age_sec", _get(summary, "scheduler_loop_last_tick_age_sec", 0)) or 0)


def _status_from_reasons(*, hard_red: bool, degraded: list[str]) -> str:
    if hard_red:
        return "RED"
    if degraded:
        return "YELLOW"
    return "GREEN"


def build_release_control_snapshot(*, limit: int = 25) -> ReleaseControlSnapshot:
    recent = get_recent_probe_runs(limit=limit)
    storage = storage_legacy_audit()
    recovery = disaster_recovery_status(include_hash=False)
    scheduler = scheduler_health_snapshot()
    payment_summary = payment_problem_summary(limit=limit)
    auto_audio = auto_audio_lock_summary(limit=limit)
    probe_statuses = _probe_statuses(recent)
    health = _runtime_health_scheduler_snapshot()

    degraded_reasons: list[str] = []
    hard_red = False

    storage_status = str(_get(storage, "status", "UNKNOWN") or "UNKNOWN")
    if storage_status != "GREEN":
        degraded_reasons.append(f"storage={storage_status}")

    recovery_status = str(_get(recovery, "status", "UNKNOWN") or "UNKNOWN")
    if recovery_status != "GREEN":
        degraded_reasons.append(f"backup={recovery_status}")

    scheduler_running = _scheduler_running(scheduler)
    scheduler_errors = _scheduler_error_count(scheduler)
    if not scheduler_running:
        degraded_reasons.append("scheduler_loop_not_running")
        hard_red = True
    if scheduler_errors > 0:
        degraded_reasons.append(f"scheduler_errors={scheduler_errors}")

    payment_problem_count = _payment_problem_count(payment_summary)
    if payment_problem_count > 0:
        degraded_reasons.append(f"payment_problems={payment_problem_count}")

    stale_auto_audio_lock_count = _auto_audio_stale_count(auto_audio)
    if stale_auto_audio_lock_count > 0:
        degraded_reasons.append(f"stale_auto_audio_locks={stale_auto_audio_lock_count}")

    if "telegram_transport" in health and health.get("telegram_transport") != "polling":
        degraded_reasons.append(f"telegram_transport={health.get('telegram_transport')}")
    if "telegram_webhook_enabled" in health and health.get("telegram_webhook_enabled") is not False:
        degraded_reasons.append("telegram_webhook_enabled")

    missing_probes = [status.probe_type for status in probe_statuses if not status.is_green]
    if missing_probes:
        degraded_reasons.append("probes=" + ",".join(missing_probes))
        hard_red = True

    status = _status_from_reasons(hard_red=hard_red, degraded=degraded_reasons)
    return ReleaseControlSnapshot(
        status=status,
        marker="PRODUCTION_READY" if status == "GREEN" else "PRODUCTION_DEGRADED",
        git_branch=_git_value("rev-parse", "--abbrev-ref", "HEAD"),
        git_commit=_git_value("rev-parse", "--short", "HEAD"),
        storage_status=storage_status,
        storage_legacy_sqlite_present=bool(_get(storage, "legacy_sqlite_present", False)),
        storage_repo_sqlite_present=bool(_get(storage, "repo_local_sqlite_present", False)),
        storage_disallowed_sqlite_connects=len(_get(storage, "disallowed_direct_sqlite_connects", []) or []),
        disaster_recovery_status=recovery_status,
        disaster_recovery_reason=str(_get(recovery, "reason", "") or ""),
        disaster_backup_count=int(_get(recovery, "backup_count", 0) or 0),
        disaster_latest_backup=_get(recovery, "latest_backup", None),
        disaster_latest_backup_size_bytes=int(_get(recovery, "latest_backup_size_bytes", 0) or 0),
        disaster_restore_target_configured=bool(_get(recovery, "restore_target_configured", False)),
        scheduler_loop_running=scheduler_running,
        scheduler_loop_error_count=scheduler_errors,
        scheduler_loop_last_error=_scheduler_last_error(scheduler),
        scheduler_loop_last_tick_age_sec=_scheduler_last_tick_age_sec(scheduler),
        payment_problem_count=payment_problem_count,
        stale_auto_audio_lock_count=stale_auto_audio_lock_count,
        probe_statuses=probe_statuses,
        recent_probe_runs=recent,
        stale_auto_audio_locks=_auto_audio_stale_locks(auto_audio),
    )


def get_release_control_snapshot(*, limit: int = 25) -> ReleaseControlSnapshot:
    return build_release_control_snapshot(limit=limit)


def _status_icon(status: str) -> str:
    return {"GREEN": "✅", "YELLOW": "⚠️", "RED": "🛑"}.get(status, "⚠️")


def format_release_control_report(*, limit: int = 25) -> str:
    snapshot = build_release_control_snapshot(limit=limit)
    lines = [
        f"Статус: {_status_icon(snapshot.status)} {snapshot.status}",
        f"Git: {snapshot.git_branch}@{snapshot.git_commit}",
        f"Storage: {snapshot.storage_status} legacy_sqlite={snapshot.storage_legacy_sqlite_present} repo_sqlite={snapshot.storage_repo_sqlite_present}",
        f"Disaster recovery: {snapshot.disaster_recovery_status} count={snapshot.disaster_backup_count} latest={snapshot.disaster_latest_backup or '-'}",
        f"Scheduler: running={snapshot.scheduler_loop_running} errors={snapshot.scheduler_loop_error_count} last_tick_age={snapshot.scheduler_loop_last_tick_age_sec}s",
    ]
    if snapshot.scheduler_loop_last_error:
        lines.append(f"Scheduler last error: {snapshot.scheduler_loop_last_error}")
    lines.extend([
        f"Проблемные платежи: {snapshot.payment_problem_count}",
        f"Stale auto-audio locks: {snapshot.stale_auto_audio_lock_count}",
        "Probes:",
    ])
    for status in snapshot.probe_statuses:
        icon = "✅" if status.is_green else "⚠️"
        lines.append(
            f"  {icon} {status.label}: {status.status}/{status.cleanup_status} rows={status.rows_touched} run={status.run_id or '-'}"
        )
    return "\n".join(lines)
