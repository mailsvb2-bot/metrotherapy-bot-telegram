from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = ROOT / "services" / "scheduler.py"
HEALTH = ROOT / "runtime" / "health_server.py"


def replace_once(path: Path, old: str, new: str, *, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0 and new in text:
        return
    if count != 1:
        raise SystemExit(f"expected exactly one {label} target in {path}, got {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    replace_once(
        SCHEDULER,
        '''    queue_size = int(len(getattr(precise, '_queue', ())))
    now_m = time.monotonic()
    return {
        'scheduler_loop_task_running': bg_running,
        'precise_scheduler_running': precise_running,
        'precise_scheduler_task_running': precise_task_running,
        'precise_scheduler_queue_size': queue_size,
        'scheduler_loop_started': bool(_bg_started_at_monotonic > 0),
        'scheduler_loop_uptime_sec': int(now_m - _bg_started_at_monotonic) if _bg_started_at_monotonic else 0,
        'scheduler_loop_iterations': int(_bg_iteration_count),
        'scheduler_loop_error_count': int(_bg_error_count),
        'scheduler_loop_last_error': _bg_last_error,
        'scheduler_loop_last_error_age_sec': int(now_m - _bg_last_error_at_monotonic) if _bg_last_error_at_monotonic else 0,
        'scheduler_loop_last_tick_age_sec': int(now_m - _bg_last_tick_at_monotonic) if _bg_last_tick_at_monotonic else 0,
    }
''',
        '''    queue_size = int(len(getattr(precise, '_queue', ())))
    now_m = time.monotonic()
    try:
        from services.payments.retry_queue import payment_retry_health_snapshot

        payment_retry = payment_retry_health_snapshot()
    except (ImportError, AttributeError, RuntimeError, OSError, ValueError, TypeError):
        payment_retry = {'payment_retry_active': -1, 'payment_retry_dead': -1}
    return {
        'scheduler_loop_task_running': bg_running,
        'precise_scheduler_running': precise_running,
        'precise_scheduler_task_running': precise_task_running,
        'precise_scheduler_queue_size': queue_size,
        'scheduler_loop_started': bool(_bg_started_at_monotonic > 0),
        'scheduler_loop_uptime_sec': int(now_m - _bg_started_at_monotonic) if _bg_started_at_monotonic else 0,
        'scheduler_loop_iterations': int(_bg_iteration_count),
        'scheduler_loop_error_count': int(_bg_error_count),
        'scheduler_loop_last_error': _bg_last_error,
        'scheduler_loop_last_error_age_sec': int(now_m - _bg_last_error_at_monotonic) if _bg_last_error_at_monotonic else 0,
        'scheduler_loop_last_tick_age_sec': int(now_m - _bg_last_tick_at_monotonic) if _bg_last_tick_at_monotonic else 0,
        **payment_retry,
    }
''',
        label="scheduler payment retry health",
    )
    replace_once(
        HEALTH,
        '''def _scheduler_readiness(scheduler: dict[str, Any]) -> tuple[bool, list[str], dict[str, bool]]:
    running = bool(scheduler.get('scheduler_loop_task_running'))
    recent_error = _scheduler_recent_error(scheduler)
    stale = _scheduler_stale(scheduler)

    errors: list[str] = []
    if not running:
        errors.append('scheduler:not_running')
    if recent_error:
        errors.append('scheduler:recent_owner_tick_error')
    if stale:
        errors.append('scheduler:stale_tick')

    return (
        bool(running and not recent_error and not stale),
        errors,
        {
            'scheduler_recent_error': recent_error,
            'scheduler_stale': stale,
            'scheduler_degraded': bool(recent_error or stale),
        },
    )
''',
        '''def _scheduler_readiness(scheduler: dict[str, Any]) -> tuple[bool, list[str], dict[str, bool]]:
    running = bool(scheduler.get('scheduler_loop_task_running'))
    recent_error = _scheduler_recent_error(scheduler)
    stale = _scheduler_stale(scheduler)
    try:
        payment_retry_active = int(scheduler.get('payment_retry_active') or 0)
    except (TypeError, ValueError):
        payment_retry_active = -1
    try:
        payment_retry_dead_count = int(scheduler.get('payment_retry_dead') or 0)
    except (TypeError, ValueError):
        payment_retry_dead_count = -1
    max_active = _int_env('PAYMENT_RETRY_READY_MAX_ACTIVE', 1000)
    max_dead = _int_env('PAYMENT_RETRY_READY_MAX_DEAD', 0)
    payment_retry_unavailable = payment_retry_active < 0 or payment_retry_dead_count < 0
    payment_retry_backlog = payment_retry_active > max_active
    payment_retry_dead = payment_retry_dead_count > max_dead

    errors: list[str] = []
    if not running:
        errors.append('scheduler:not_running')
    if recent_error:
        errors.append('scheduler:recent_owner_tick_error')
    if stale:
        errors.append('scheduler:stale_tick')
    if payment_retry_unavailable:
        errors.append('payment_retry:unavailable')
    if payment_retry_backlog:
        errors.append('payment_retry:backlog')
    if payment_retry_dead:
        errors.append('payment_retry:dead_letter')

    degraded = bool(
        recent_error
        or stale
        or payment_retry_unavailable
        or payment_retry_backlog
        or payment_retry_dead
    )
    return (
        bool(running and not degraded),
        errors,
        {
            'scheduler_recent_error': recent_error,
            'scheduler_stale': stale,
            'payment_retry_unavailable': payment_retry_unavailable,
            'payment_retry_backlog': payment_retry_backlog,
            'payment_retry_dead_letter': payment_retry_dead,
            'scheduler_degraded': degraded,
        },
    )
''',
        label="payment retry readiness",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
