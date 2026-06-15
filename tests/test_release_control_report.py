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


def _green_runs() -> list[ProbeRun]:
    return [
        _probe("payment_entitlement_reconciliation_probe", idx=1),
        _probe("probe_scheduler_job_live", idx=2),
        _probe("auto_audio_dry_run_probe", idx=3),
    ]


def test_release_report_green_when_storage_and_probes_are_green(monkeypatch) -> None:
    monkeypatch.setattr(report_module, "get_recent_probe_runs", lambda limit: _green_runs())
    monkeypatch.setattr(report_module, "payment_problem_summary", lambda limit: [])
    monkeypatch.setattr(report_module, "auto_audio_lock_summary", lambda limit: {"stale_lock_count": 0, "locks": []})
    monkeypatch.setattr(report_module, "storage_legacy_audit", lambda: _Storage("GREEN"))
    monkeypatch.setattr(report_module, "_git_value", lambda *args: "main" if "--abbrev-ref" in args else "abc123")

    text = report_module.format_release_control_report(limit=10)
    snapshot = report_module.build_release_control_snapshot(limit=10)

    assert snapshot.status == "GREEN"
    assert "Статус: ✅ GREEN" in text
    assert "Storage: GREEN legacy_sqlite=False" in text
    assert "Проблемные платежи: 0" in text
    assert "Stale auto-audio locks: 0" in text


def test_release_report_yellow_when_storage_is_yellow(monkeypatch) -> None:
    monkeypatch.setattr(report_module, "get_recent_probe_runs", lambda limit: _green_runs())
    monkeypatch.setattr(report_module, "payment_problem_summary", lambda limit: [])
    monkeypatch.setattr(report_module, "auto_audio_lock_summary", lambda limit: {"stale_lock_count": 0, "locks": []})
    monkeypatch.setattr(report_module, "storage_legacy_audit", lambda: _Storage("YELLOW"))
    monkeypatch.setattr(report_module, "_git_value", lambda *args: "main")

    text = report_module.format_release_control_report(limit=10)

    assert "Статус: ⚠️ YELLOW" in text
    assert "Storage: YELLOW" in text


def test_release_report_red_when_required_probe_missing(monkeypatch) -> None:
    monkeypatch.setattr(report_module, "get_recent_probe_runs", lambda limit: [_probe("probe_scheduler_job_live", idx=2)])
    monkeypatch.setattr(report_module, "payment_problem_summary", lambda limit: [])
    monkeypatch.setattr(report_module, "auto_audio_lock_summary", lambda limit: {"stale_lock_count": 0, "locks": []})
    monkeypatch.setattr(report_module, "storage_legacy_audit", lambda: _Storage("GREEN"))
    monkeypatch.setattr(report_module, "_git_value", lambda *args: "main")

    text = report_module.format_release_control_report(limit=10)

    assert "Статус: 🛑 RED" in text
    assert "missing/missing" in text
