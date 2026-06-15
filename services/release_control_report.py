from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.auto_audio_recovery import auto_audio_lock_summary
from services.payments.reconciliation import payment_problem_summary
from services.probe_ledger import ProbeRun, get_recent_probe_runs
from services.storage_legacy_audit import storage_legacy_audit

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PROBES = {
    "payment_entitlement_reconciliation_probe": "💳 Payment entitlement",
    "probe_scheduler_job_live": "⏱ Scheduler job",
    "auto_audio_dry_run_probe": "🎧 Auto-audio dry-run",
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
    payment_problem_count: int
    stale_auto_audio_lock_count: int
    probe_statuses: list[ReleaseProbeStatus]
    recent_probe_runs: list[ProbeRun]
    stale_auto_audio_locks: list[dict[str, Any]]



def _git_value(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
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
                    cleanup_status="missing",
                    rows_touched=0,
                    run_id="",
                    finished_at_utc=None,
                    error="no recent probe ledger record",
                )
            )
            continue
        statuses.append(
            ReleaseProbeStatus(
                probe_type=probe_type,
                label=label,
                status=run.status,
                cleanup_status=run.cleanup_status,
                rows_touched=run.rows_touched,
                run_id=run.run_id,
                finished_at_utc=run.finished_at_utc,
                error=run.error,
            )
        )
    return statuses


def _overall_marker(
    *,
    statuses: list[ReleaseProbeStatus],
    storage_status: str,
    payment_problem_count: int,
    stale_auto_audio_lock_count: int,
) -> tuple[str, str]:
    if storage_status == "RED":
        return "🛑", "RED"
    if not all(item.is_green for item in statuses):
        return "🛑", "RED"
    if storage_status != "GREEN":
        return "⚠️", "YELLOW"
    if payment_problem_count > 0:
        return "⚠️", "YELLOW"
    if stale_auto_audio_lock_count > 0:
        return "⚠️", "YELLOW"
    return "✅", "GREEN"


def _short(value: str | None, *, length: int = 12) -> str:
    if not value:
        return "-"
    return str(value)[:length]


def build_release_control_snapshot(*, limit: int = 25) -> ReleaseControlSnapshot:
    runs = get_recent_probe_runs(limit=max(int(limit), len(REQUIRED_PROBES)))
    statuses = _probe_statuses(runs)
    payment_problem_count = len(payment_problem_summary(limit=20))
    auto_audio_locks = auto_audio_lock_summary(limit=5)
    stale_auto_audio_lock_count = int(auto_audio_locks.get("stale_lock_count") or 0)
    storage = storage_legacy_audit()
    marker, status = _overall_marker(
        statuses=statuses,
        storage_status=str(storage.status),
        payment_problem_count=payment_problem_count,
        stale_auto_audio_lock_count=stale_auto_audio_lock_count,
    )
    return ReleaseControlSnapshot(
        status=status,
        marker=marker,
        git_branch=_git_value("rev-parse", "--abbrev-ref", "HEAD"),
        git_commit=_git_value("rev-parse", "--short", "HEAD"),
        storage_status=str(storage.status),
        storage_legacy_sqlite_present=bool(storage.legacy_sqlite_present),
        storage_repo_sqlite_present=bool(storage.repo_local_sqlite_present),
        storage_disallowed_sqlite_connects=len(storage.disallowed_direct_sqlite_connects),
        payment_problem_count=payment_problem_count,
        stale_auto_audio_lock_count=stale_auto_audio_lock_count,
        probe_statuses=statuses,
        recent_probe_runs=runs,
        stale_auto_audio_locks=list(auto_audio_locks.get("locks", []) or []),
    )


def format_release_control_report(*, limit: int = 25) -> str:
    """Return an admin-facing release/control-plane status summary.

    The report is read-only: it does not run probes, mutate rows, contact external
    providers, or restart services. It only summarizes the latest already-recorded
    probe ledger facts and current payment/storage/delivery problem surfaces.
    """
    snapshot = build_release_control_snapshot(limit=limit)
    lines = [
        "🚦 Release gate / control-plane",
        "",
        f"Статус: {snapshot.marker} {snapshot.status}",
        f"Git: {snapshot.git_branch} @ {snapshot.git_commit}",
        f"Storage: {snapshot.storage_status} "
        f"legacy_sqlite={snapshot.storage_legacy_sqlite_present} "
        f"repo_sqlite={snapshot.storage_repo_sqlite_present} "
        f"bad_sqlite_connects={snapshot.storage_disallowed_sqlite_connects}",
        f"Проблемные платежи: {snapshot.payment_problem_count}",
        f"Stale auto-audio locks: {snapshot.stale_auto_audio_lock_count}",
        "",
        "Обязательные proof-проверки:",
    ]

    for item in snapshot.probe_statuses:
        probe_marker = "✅" if item.is_green else "⚠️"
        lines.append(
            f"{probe_marker} {item.label}: {item.status}/{item.cleanup_status} "
            f"rows={item.rows_touched} run={_short(item.run_id)}"
        )
        if item.finished_at_utc:
            lines.append(f"   finished={item.finished_at_utc}")
        if item.error:
            lines.append(f"   error={item.error[:180]}")

    if snapshot.stale_auto_audio_lock_count:
        lines.extend(["", "Stale auto-audio delivery locks:"])
        for item in snapshot.stale_auto_audio_locks[:5]:
            lines.append(
                "⚠️ "
                f"user_id={item.get('user_id')} "
                f"stage={item.get('stage')} "
                f"kind={item.get('kind')} "
                f"age={item.get('age_seconds')}s"
            )

    lines.extend(
        [
            "",
            "Последние probe ledger записи:",
        ]
    )
    for run in snapshot.recent_probe_runs[:7]:
        run_marker = "✅" if run.status == "ok" and run.cleanup_status in {"clean", "dry_run"} else "⚠️"
        lines.append(
            f"{run_marker} #{run.id} {run.probe_type} — {run.status}/{run.cleanup_status} "
            f"rows={run.rows_touched} run={_short(run.run_id)}"
        )

    if snapshot.status == "GREEN":
        lines.append("\nИтог: релизный контур выглядит зелёным по последним proof-записям.")
    elif snapshot.status == "YELLOW":
        lines.append("\nИтог: probes зелёные, но есть storage/payment/auto-audio пункт для ручной проверки.")
    else:
        lines.append("\nИтог: есть незакрытая proof-проблема. Релиз/изменения нужно остановить до разбора.")

    return "\n".join(lines)


__all__ = ["ReleaseControlSnapshot", "ReleaseProbeStatus", "build_release_control_snapshot", "format_release_control_report"]
