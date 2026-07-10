from __future__ import annotations

import pytest

from services import growth_conversion_event_bridge as bridge
from services import growth_conversion_runtime_report
from services import scheduler


@pytest.mark.asyncio
async def test_scheduler_runs_growth_bridge_through_safe_boundary(monkeypatch):
    captured = {}

    def _fake_runner(*, batch_size: int):
        captured["batch_size"] = batch_size
        return bridge.EventBridgeResult(processed=2, inserted=2)

    monkeypatch.setattr(bridge, "run_event_conversion_bridge_safe", _fake_runner)
    monkeypatch.setenv("GROWTH_CONVERSION_BRIDGE_BATCH_SIZE", "73")

    await scheduler._run_growth_conversion_bridge_tick()

    assert captured["batch_size"] == 73


def test_combined_conversion_report_exposes_bridge_cursor(monkeypatch):
    monkeypatch.setattr(
        growth_conversion_runtime_report,
        "build_conversion_hub_report",
        lambda period: f"hub:{period}",
    )
    monkeypatch.setattr(
        growth_conversion_runtime_report,
        "event_conversion_bridge_snapshot",
        lambda: {
            "ok": True,
            "last_event_id": 123,
            "last_batch_size": 4,
            "last_inserted": 3,
            "last_duplicates": 1,
            "updated_at": "2026-07-10T10:00:00+00:00",
        },
    )

    text = growth_conversion_runtime_report.build_growth_conversion_runtime_report("week")

    assert "hub:week" in text
    assert "cursor event_id: 123" in text
    assert "last batch: 4" in text
    assert "inserted: 3" in text
    assert "duplicates: 1" in text
