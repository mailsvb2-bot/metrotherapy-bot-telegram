from __future__ import annotations

import asyncio
import logging

import pytest

from core.runtime_env import env_float, env_int
from core.telegram_bot import build_bot
from services import scheduler


def test_runtime_env_rejects_invalid_non_finite_and_out_of_range_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_FLOAT", "nan")
    assert env_float("TEST_FLOAT", 2.5, minimum=0.0, maximum=10.0) == 2.5

    monkeypatch.setenv("TEST_FLOAT", "inf")
    assert env_float("TEST_FLOAT", 2.5, minimum=0.0, maximum=10.0) == 2.5

    monkeypatch.setenv("TEST_FLOAT", "-1")
    assert env_float("TEST_FLOAT", 2.5, minimum=0.0, maximum=10.0) == 2.5

    monkeypatch.setenv("TEST_INT", "broken")
    assert env_int("TEST_INT", 3, minimum=0, maximum=5) == 3

    monkeypatch.setenv("TEST_INT", "99")
    assert env_int("TEST_INT", 3, minimum=0, maximum=5) == 3


def test_telegram_runtime_settings_fall_back_instead_of_crashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_REQUEST_TIMEOUT_SEC", "nan")
    monkeypatch.setenv("TELEGRAM_NETWORK_RETRIES", "999999")
    monkeypatch.setenv("TELEGRAM_NETWORK_RETRY_DELAY_SEC", "-4")

    bot = build_bot("123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
    try:
        assert bot._request_timeout == 20.0
        assert bot._network_retries == 2
        assert bot._network_retry_delay == 0.75
    finally:
        asyncio.run(bot.session.close())


@pytest.mark.asyncio
async def test_scheduler_owner_does_not_overlap_same_owner() -> None:
    scheduler._owner_tasks.clear()
    scheduler._owner_started_at.clear()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def owner() -> None:
        entered.set()
        await release.wait()

    first = scheduler._start_owner_tick("owner-test", owner)
    assert first is not None
    await entered.wait()
    assert scheduler._start_owner_tick("owner-test", owner) is None

    release.set()
    await first
    await asyncio.sleep(0)
    assert "owner-test" not in scheduler._owner_tasks


@pytest.mark.asyncio
async def test_scheduler_error_diagnostics_do_not_retain_exception_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "provider-token=must-not-appear"

    async def fail() -> None:
        raise RuntimeError(secret)

    with caplog.at_level(logging.ERROR):
        result = await scheduler._run_protected_tick("privacy-test", fail)

    assert result is False
    assert secret not in scheduler._bg_last_error
    assert "privacy-test:RuntimeError" in scheduler._bg_last_error
