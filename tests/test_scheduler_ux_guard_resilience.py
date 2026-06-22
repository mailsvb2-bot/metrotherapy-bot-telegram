from __future__ import annotations

import sqlite3

import pytest

from services import scheduler


@pytest.mark.asyncio
async def test_safe_ux_guard_handles_sqlite_operational_error(monkeypatch):
    async def _boom():
        raise sqlite3.OperationalError("no such table: ux_guard_events")

    monkeypatch.setattr(scheduler, "_run_ux_guard_tick", _boom)

    await scheduler._safe_ux_guard_tick()


@pytest.mark.asyncio
async def test_safe_ux_guard_handles_unexpected_error(monkeypatch):
    async def _boom():
        raise RuntimeError("analytics temporarily unavailable")

    monkeypatch.setattr(scheduler, "_run_ux_guard_tick", _boom)

    await scheduler._safe_ux_guard_tick()
