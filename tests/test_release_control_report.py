from __future__ import annotations

from services.probe_ledger import ProbeRun
import services.release_control_report as report_module


def _probe(probe_type: str, *, status: str = "ok", cleanup_status: str = "clean", idx: int = 1) -> ProbeRun:
    return ProbeRun(
        id=idx,
        probe_type=probe_type,
        run_id=f"run-{idx}",
        user_id=None,
        started_at_utc="2026-06-15T20:00:00Z",
        finished_at_utc="2026-06-15T20:00:01Z",
        status=status,
        cleanup_status=cleanup_status,
        rows_touched=idx,
        error=None,
        evidence={},
    )


class _Storage:
    def __init__(self, status: str = "GREEN") -> None:
        self.status = status
        self.legacy_sqlite_present = status != "GREEN"
        self.repo_local_sqlite_present = False
        self.disallowed_direct_sqlite_connects = []


class _Recovery:
    def __init__(self, status: str = "GREEN") -> None:
        self.status = status
        self.reason = "ok" if status == "GREEN" else "backup_missing"
        self.backup_count = 1 if status == "GREEN" else 0
        self.latest_backup = "/tmp/backup.dump" if status == "GREEN" else None
        self.latest_backup_size_bytes = 1024 if status == "GREEN" else 0
        self.restore_target_configured = status == "GREEN"


def _scheduler_health(*, running: bool = True, errors: int = 0) -> dict[str, object]:
    return {
        "scheduler_loop_task_running": running,
        "scheduler_loop_error_count": errors,
        "scheduler_loop_last_error": "unit-test-error" if errors else "",
        "scheduler_loop_last_tick_age_sec": 1,
    }


def _green_runs() -> list[ProbeRun]:
    return [
        _probe("payment_entitlement_reconciliation_probe", idx=1),
        _probe("probe_scheduler_job_live", idx=2),
        _probe("auto_audio_dry_run_probe", idx=3),
        _probe("synthetic_user_journey_e2e_probe", idx=4),
    ]


def _patch_common(monkeypatch, *, storage: str = "GREEN", recovery: str = "GREEN", scheduler_running: bool = True, scheduler_errors: int = 0) -> None:
    monkeypatch.setattr(report_module, "get_recent_probe_runs", lambda limit: _green_runs())
    monkeypatch.setattr(report_module, "payment_problem_summary", lambda limit: [])
    monkeypatch.setattr(report_module, "auto_audio_lock_summary", lambda limit: {"stale_lock_count": 0, "locks": []})
    monkeypatch.setattr(report_module, "storage_legacy_audit", lambda: _Storage(storage))
    monkeypatch.setattr(report_module, "disaster_recovery_status", lambda include_hash=False: _Recovery(recovery))
    monkeypatch.setattr(report_module, "scheduler_health_snapshot", lambda: _scheduler_health(running=scheduler_running, errors=scheduler_errors))
    monkeypatch.setattr(report_module, "_runtime_health_scheduler_snapshot", lambda: {})
    monkeypatch.setattr(report_module, "_git_value", lambda *args: "main" if "--abbrev-ref" in args else "abc123")


def test_release_report_green_when_storage_recovery_and_probes_are_green(monkeypatch) -> None:
    _patch_common(monkeypatch)

    text = report_module.format_release_control_report(limit=10)
    snapshot = report_module.build_release_control_snapshot(limit=10)

    assert snapshot.status == "GREEN"
    assert "Статус: ✅ GREEN" in text
    assert "Storage: GREEN legacy_sqlite=False" in text
    assert "Disaster recovery: GREEN" in text
    assert "Scheduler: running=True errors=0" in text
    assert "Проблемные платежи: 0" in text
    assert "Stale auto-audio locks: 0" in text
    assert "User journey E2E: ok/clean" in text


def test_release_report_yellow_when_storage_is_yellow(monkeypatch) -> None:
    _patch_common(monkeypatch, storage="YELLOW")

    text = report_module.format_release_control_report(limit=10)

    assert "Статус: ⚠️ YELLOW" in text
    assert "Storage: YELLOW" in text


def test_release_report_yellow_when_recovery_is_not_green(monkeypatch) -> None:
    _patch_common(monkeypatch, recovery="RED")

    text = report_module.format_release_control_report(limit=10)

    assert "Статус: ⚠️ YELLOW" in text
    assert "Disaster recovery: RED" in text


def test_release_report_yellow_when_scheduler_has_errors(monkeypatch) -> None:
    _patch_common(monkeypatch, scheduler_errors=2)

    text = report_module.format_release_control_report(limit=10)

    assert "Статус: ⚠️ YELLOW" in text
    assert "Scheduler: running=True errors=2" in text
    assert "Scheduler last error: unit-test-error" in text


def test_release_report_red_when_scheduler_is_not_running(monkeypatch) -> None:
    _patch_common(monkeypatch, scheduler_running=False)

    text = report_module.format_release_control_report(limit=10)

    assert "Статус: 🛑 RED" in text
    assert "Scheduler: running=False" in text


def test_release_report_red_when_required_probe_missing(monkeypatch) -> None:
    monkeypatch.setattr(report_module, "get_recent_probe_runs", lambda limit: [_probe("probe_scheduler_job_live", idx=2)])
    monkeypatch.setattr(report_module, "payment_problem_summary", lambda limit: [])
    monkeypatch.setattr(report_module, "auto_audio_lock_summary", lambda limit: {"stale_lock_count": 0, "locks": []})
    monkeypatch.setattr(report_module, "storage_legacy_audit", lambda: _Storage("GREEN"))
    monkeypatch.setattr(report_module, "disaster_recovery_status", lambda include_hash=False: _Recovery("GREEN"))
    monkeypatch.setattr(report_module, "scheduler_health_snapshot", lambda: _scheduler_health())
    monkeypatch.setattr(report_module, "_runtime_health_scheduler_snapshot", lambda: {})
    monkeypatch.setattr(report_module, "_git_value", lambda *args: "main")

    text = report_module.format_release_control_report(limit=10)

    assert "Статус: 🛑 RED" in text
    assert "missing/missing" in text
    assert "User journey E2E: missing/missing" in text


def test_release_report_requires_user_journey_e2e_probe() -> None:
    assert "synthetic_user_journey_e2e_probe" in report_module.REQUIRED_PROBES
