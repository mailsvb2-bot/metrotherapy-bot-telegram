from __future__ import annotations


import asyncio
import heapq
import logging
import os
import sqlite3
import time
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

if TYPE_CHECKING:
    from aiogram import Bot

log = logging.getLogger(__name__)



def _coro_name(coro: Awaitable[None]) -> str:
    try:
        return getattr(coro, "__name__")  # type: ignore[attr-defined]
    except (AttributeError, TypeError):  # validator: allow-except-exception
        pass
    try:
        code = getattr(coro, "cr_code", None)
        if code is not None:
            return getattr(code, "co_name", "<coro>")
    except (AttributeError, TypeError, ValueError):  # validator: allow-except-exception
        pass
    return type(coro).__name__


def _tm_create(coro: Awaitable[None]) -> asyncio.Task:
    """Создаём фоновые задачи строго через TaskManager (единый lifecycle + логирование)."""
    def _attach(t: asyncio.Task) -> asyncio.Task:
        # Даже в fallback-режиме не теряем исключения фоновой задачи.
        def _done_cb(task: asyncio.Task) -> None:
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                return
            except (asyncio.InvalidStateError, RuntimeError):  # validator: allow-except-exception
                log.exception("Background task callback failed task=%s", _coro_name(coro))
                return
            if exc is not None:
                log.error(
                    "Background task crashed task=%s",
                    _coro_name(coro),
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        t.add_done_callback(_done_cb)
        return t

    try:
        from services.bg import tm

        return _attach(tm().create(coro))
    except (ImportError, AttributeError):
        # Last resort: TaskManager недоступен (например, урезанная сборка).
        log.exception("TaskManager unavailable; falling back to asyncio.create_task task=%s", _coro_name(coro))
        return _attach(asyncio.create_task(coro))
    except RuntimeError:
        # Например, loop уже закрыт/не тот контекст.
        log.exception("TaskManager runtime error; falling back to asyncio.create_task task=%s", _coro_name(coro))
        return _attach(asyncio.create_task(coro))
    except OSError:
        # Системный сбой при доступе к loop/ресурсам — не скрываем, а логируем и уходим в asyncio fallback.
        log.exception("TaskManager OS error; falling back to asyncio.create_task task=%s", _coro_name(coro))
        return _attach(asyncio.create_task(coro))


class PreciseScheduler:
    """In-memory precise scheduler based on monotonic loop.time().

    Stores *factories* (callables) instead of created coroutine objects.
    """

    def __init__(self) -> None:
        self._queue: list[tuple[float, int, Callable[[], Awaitable[None]]]] = []
        self._cv = asyncio.Condition()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._seq = 0

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = _tm_create(self._run())

    async def stop(self) -> None:
        self._running = False
        async with self._cv:
            self._cv.notify_all()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def schedule_at(self, when_loop_time: float, factory: Callable[[], Awaitable[None]]) -> None:
        async with self._cv:
            self._seq += 1
            heapq.heappush(self._queue, (when_loop_time, self._seq, factory))
            self._cv.notify()

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                async with self._cv:
                    while self._running and not self._queue:
                        await self._cv.wait()
                    if not self._running:
                        break
                    when_t, _, factory = self._queue[0]
                    delay = when_t - loop.time()
                    if delay > 0:
                        try:
                            await asyncio.wait_for(self._cv.wait(), timeout=delay)
                            continue
                        except asyncio.TimeoutError:
                            pass
                    heapq.heappop(self._queue)
                await factory()
            except asyncio.CancelledError:
                break
            except (RuntimeError, OSError) as e:
                log.exception("PreciseScheduler task failed: %s", e)


_precise: Optional[PreciseScheduler] = None
_bg_task: Optional[asyncio.Task] = None


def get_precise_scheduler() -> PreciseScheduler:
    global _precise
    if _precise is None:
        _precise = PreciseScheduler()
    return _precise


def scheduler_health_snapshot() -> dict[str, bool | int]:
    """Cheap runtime diagnostics for health endpoint / ops checks."""
    precise = get_precise_scheduler()
    precise_task = getattr(precise, '_task', None)
    bg_running = bool(_bg_task is not None and not _bg_task.done())
    precise_running = bool(getattr(precise, '_running', False))
    precise_task_running = bool(precise_task is not None and not precise_task.done())
    queue_size = int(len(getattr(precise, '_queue', ())))
    return {
        'scheduler_loop_task_running': bg_running,
        'precise_scheduler_running': precise_running,
        'precise_scheduler_task_running': precise_task_running,
        'precise_scheduler_queue_size': queue_size,
    }


# NOTE (v16.3): scheduled_jobs scheduler removed.
# We keep only one persistent scheduler: services.jobs (run_at_utc ISO) executed by core.engine.Engine.tick.
# This file still keeps PreciseScheduler for in-memory timers (if ever used).


async def _run_ux_guard_tick() -> None:
    """Run UX guard once without allowing diagnostics failures to kill scheduler.

    UX guard is intentionally best-effort and read-only. It must never become the
    owner of scheduler liveness: stale schemas, temporary DB locks, validator bugs
    or unexpected analytics exceptions should be visible in logs but must not stop
    auto-audio, jobs, payments follow-ups or engine ticks.
    """
    from services.ai.ux_guard import analyze
    from services.db import get_db

    app_env = (os.getenv("APP_ENV") or "dev").lower()
    timeout = 2.0 if app_env == "prod" else 5.0

    def _run() -> None:
        # Enforce read-only mode even if analyze() misbehaves.
        with get_db() as conn:
            try:
                conn.execute("PRAGMA query_only=ON")
            except (sqlite3.Error, OSError, RuntimeError):  # validator: allow-wide-except
                # SQLite/Postgres compatibility path may not support this PRAGMA.
                pass
            analyze(conn)

    await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)


async def _safe_ux_guard_tick() -> None:
    try:
        await _run_ux_guard_tick()
    except asyncio.TimeoutError:
        log.warning("UX guard timed out")
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            log.debug("UX guard skipped until schema is ready", exc_info=True)
            return
        log.exception("UX guard DB operational error")
    except (sqlite3.Error, RuntimeError, OSError, ValueError, TypeError, AttributeError, KeyError):
        log.exception("UX guard failed")
    except Exception:  # validator: allow-wide-except
        log.exception("UX guard unexpected failure")


async def _background_loop(bot: 'Bot') -> None:
    """Non-SLA background ticks (reduced frequency).

    IMPORTANT: This is the canonical place for lightweight background ticks.
    We avoid direct asyncio.create_task usage in app.py; validator expects scheduler-managed background work.
    """
    from services.auto_audio import tick as auto_audio_tick
    from core.engine import engine
    from core.runtime.self_healing import SelfHealingEngine
    from core.ai.reward_engine import compute_and_store_rewards

    self_heal = SelfHealingEngine()
    last_heal = 0.0
    heal_interval = float(os.getenv('SELF_HEAL_INTERVAL_SEC', '5') or '5')
    heal_interval = max(1.0, heal_interval)

    last_ux_guard = 0.0
    last_reward = 0.0
    reward_interval = float(os.getenv('REWARD_TICK_INTERVAL_SEC', '60') or '60')
    reward_interval = max(10.0, reward_interval)
    while True:
        await asyncio.sleep(1)
        # Self-healing tick (best-effort, no side effects except SAFE_MODE state)
        try:
            now_m = time.monotonic()
            if now_m - last_heal >= heal_interval:
                last_heal = now_m
                self_heal.tick()
        except Exception:  # validator: allow-wide-except
            log.exception('SelfHealingEngine.tick failed')
        try:
            await auto_audio_tick(bot)
        except (RuntimeError, OSError) as e:
            log.exception("Scheduler error: %s", e)
            log.exception("auto_audio_tick failed")
        try:
            await engine.tick(bot)
        except (RuntimeError, OSError) as e:
            log.exception("Scheduler error: %s", e)
            log.exception("engine.tick failed")
        # RewardEngine tick (best-effort, writes aggregated rewards)
        try:
            now_m = time.monotonic()
            if now_m - last_reward >= reward_interval:
                last_reward = now_m
                app_env = (os.getenv("APP_ENV") or "dev").lower()
                reward_timeout = 5.0 if app_env == "prod" else 15.0
                reward_window_sec = int(os.getenv('REWARD_WINDOW_SEC','3600') or '3600')
                reward_lookback_h = int(os.getenv('REWARD_LOOKBACK_H','24') or '24')
                reward_task = asyncio.to_thread(
                    compute_and_store_rewards,
                    reward_window_sec,
                    lookback_hours=reward_lookback_h,
                )
                await asyncio.wait_for(reward_task, timeout=reward_timeout)
        except Exception:  # validator: allow-wide-except
            log.exception('RewardEngine tick failed')
        # UX guard (best-effort)
        # Runs rarely and MUST be read-only (SQLite query_only=ON), to avoid competing with writes.
        # Interval can be tuned via UX_GUARD_INTERVAL_SEC.
        now_m = time.monotonic()
        app_env = (os.getenv("APP_ENV") or "dev").lower()
        default_interval = 60.0 if app_env == "prod" else 10.0
        interval = float(os.getenv("UX_GUARD_INTERVAL_SEC", str(default_interval)) or str(default_interval))
        interval = max(10.0, interval)  # never spam
        if now_m - last_ux_guard >= interval:
            last_ux_guard = now_m
            await _safe_ux_guard_tick()


def start_scheduler(bot: 'Bot') -> None:
    """Start scheduler loops. Keeps compatibility with app.py."""
    global _bg_task

    # PreciseScheduler is kept for potential in-memory timers, but DB-backed jobs are executed by Engine.tick.
    async def runner_bg():
        await get_precise_scheduler().start()
        await _background_loop(bot)

    if not _bg_task or _bg_task.done():
        _bg_task = _tm_create(runner_bg())


async def stop_scheduler() -> None:
    """Stop all loops + PreciseScheduler (no leaks)."""
    global _bg_task

    for t in (_bg_task,):
        if t and not t.done():
            t.cancel()
    for t in (_bg_task,):
        if t:
            try:
                await t
            except asyncio.CancelledError:
                pass

    _bg_task = None
    await get_precise_scheduler().stop()

# Resume pending/running jobs on startup
# NOTE: enqueue idempotency is enforced by jobs.job_key UNIQUE index + INSERT OR IGNORE in services/jobs.py
