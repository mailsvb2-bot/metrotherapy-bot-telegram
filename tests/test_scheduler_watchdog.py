from __future__ import annotations

import pytest

from services import scheduler


@pytest.mark.asyncio
async def test_protected_tick_catches_unexpected_exception() -> None:
    before = int(getattr(scheduler, "_bg_error_count", 0))

    async def boom() -> None:
        raise ValueError("synthetic scheduler tick failure")

    ok = await scheduler._run_protected_tick("unit-test", boom)

    assert ok is False
    assert int(getattr(scheduler, "_bg_error_count", 0)) == before + 1
    assert "unit-test:ValueError" in str(getattr(scheduler, "_bg_last_error", ""))


def test_scheduler_health_exposes_watchdog_fields() -> None:
    snapshot = scheduler.scheduler_health_snapshot()

    assert "scheduler_loop_error_count" in snapshot
    assert "scheduler_loop_last_error" in snapshot
    assert "scheduler_loop_iterations" in snapshot
    assert "scheduler_loop_last_tick_age_sec" in snapshot
