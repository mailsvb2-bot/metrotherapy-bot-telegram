from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from services.admin_payment_report import payment_problem_summary
from services.auto_audio import auto_audio_lock_summary
from services.disaster_recovery_status import disaster_recovery_status
from services.probe_ledger import ProbeRun, get_recent_probe_runs
from services.scheduler import scheduler_health_snapshot
from services.storage_legacy_audit import storage_legacy_audit

DEFAULT_HEALTH_URL = "http://127.0.0.1:8088/healthz"


@dataclass(frozen=True)
class ReleaseProbeStatus:
    probe_type: str
    label: str
    status: str
    cleanup_status: str
    rows_touched: int
    run_id: str
    finished_at_utc: datetime | None
    error: str | None


@dataclass(frozen=True)
class ReleaseControlSnapshot:
    generated_at_utc: datetime
    overall_status: str
    degraded_reasons: list[str]
    probe_statuses: list[ReleaseProbeStatus]
    storage_status: str
    backup_status: str
    scheduler_loop_running: bool
    scheduler_error_count: int
    payment_problem_count: int
    stale_auto_audio_locks: int
    health_payload: dict[str, Any]


_REQUIRED_PROBES = (
    ("post_deploy", "Post-deploy verify"),
    ("db_restore", "DB restore drill"),
    ("postgres_concurrency", "Postgres concurrency"),
    ("auto_audio", "Auto-audio dry-run"),
)


def _latest_by_probe_type(runs: list[ProbeRun]) -> dict[str, ProbeRun]:
    out: dict[str, ProbeRun] = {}
    for run in runs or []:
        probe_type = str(run.probe_type or "")
        if not probe_type:
            continue
        current = out.get(probe_type)
        if current is None:
            out[probe_type] = run
            continue
        if (run.finished_at_utc or "") > (current.finished_at_utc or ""):
            out[probe_type] = run
    return out


def _probe_statuses(runs: list[ProbeRun]) -> list[ReleaseProbeStatus]:
    latest = _latest_by_probe_type(runs)
    statuses: list[ReleaseProbeStatus] = []
    for probe_type, label in _REQUIRED_PROBES:
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
    except (urllib.error.URLError, TimeoutError, OSError):
        return {}
    except json.JSONDecodeError:
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
    for status in probe_statuses:
        if status.status != "ok":
            degraded_reasons.append(f"probe_{status.probe_type}={status.status}")

    overall = "GREEN" if not degraded_reasons else "YELLOW"
    return ReleaseControlSnapshot(
        generated_at_utc=datetime.utcnow(),
        overall_status=overall,
        degraded_reasons=degraded_reasons,
        probe_statuses=probe_statuses,
        storage_status=storage.status,
        backup_status=recovery.status,
        scheduler_loop_running=bool(scheduler.loop_running),
        scheduler_error_count=int(scheduler.loop_error_count or 0),
        payment_problem_count=int(payment_summary.problem_count or 0),
        stale_auto_audio_locks=int(auto_audio.stale_count or 0),
        health_payload=health,
    )


def format_release_control_report(snapshot: ReleaseControlSnapshot | None = None) -> str:
    snap = snapshot or get_release_control_snapshot()
    lines = [
        "🚦 Release control",
        f"Status: {snap.overall_status}",
        f"Storage: {snap.storage_status}",
        f"Backup: {snap.backup_status}",
        f"Scheduler: {'ON' if snap.scheduler_loop_running else 'OFF'} (errors={snap.scheduler_error_count})",
        f"Payment problems: {snap.payment_problem_count}",
        f"Stale auto-audio locks: {snap.stale_auto_audio_locks}",
        "",
        "Probes:",
    ]
    for probe in snap.probe_statuses:
        lines.append(
            f"— {probe.label}: {probe.status} "
            f"cleanup={probe.cleanup_status or '-'} rows={probe.rows_touched} "
            f"run={probe.run_id or '-'}"
        )
        if probe.error:
            lines.append(f"  error={probe.error}")
    if snap.degraded_reasons:
        lines.append("")
        lines.append("Degraded reasons:")
        for reason in snap.degraded_reasons[:20]:
            lines.append(f"— {reason}")
    return "\n".join(lines)
