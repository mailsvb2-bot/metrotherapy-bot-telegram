from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "services" / "scheduler.py"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count == 0 and new in text:
        return text
    if count != 1:
        raise SystemExit(f"expected exactly one {label} target, got {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''async def _run_growth_conversion_bridge_tick() -> None:
    from services.growth_conversion_event_bridge import run_event_conversion_bridge_safe

    batch_size = int(os.getenv("GROWTH_CONVERSION_BRIDGE_BATCH_SIZE", "100") or "100")
    result = await asyncio.to_thread(run_event_conversion_bridge_safe, batch_size=batch_size)
    if result.error:
        log.warning("Growth conversion bridge degraded: %s", result.error)


''',
        '''async def _run_growth_conversion_bridge_tick() -> None:
    from services.growth_conversion_event_bridge import run_event_conversion_bridge_safe

    batch_size = int(os.getenv("GROWTH_CONVERSION_BRIDGE_BATCH_SIZE", "100") or "100")
    result = await asyncio.to_thread(run_event_conversion_bridge_safe, batch_size=batch_size)
    if result.error:
        log.warning("Growth conversion bridge degraded: %s", result.error)


async def _run_payment_reconciliation_retry_tick() -> None:
    from services.payments.retry_queue import run_payment_retry_batch

    result = await asyncio.to_thread(run_payment_retry_batch)
    if result.dead:
        log.error(
            "Payment reconciliation retries dead-lettered: claimed=%s completed=%s rescheduled=%s dead=%s",
            result.claimed,
            result.completed,
            result.rescheduled,
            result.dead,
        )
    elif result.claimed:
        log.info(
            "Payment reconciliation retry tick: claimed=%s completed=%s rescheduled=%s",
            result.claimed,
            result.completed,
            result.rescheduled,
        )


''',
        label="payment retry tick function",
    )
    text = replace_once(
        text,
        '''    last_ux_guard = 0.0
    last_reward = 0.0
    last_growth_conversion_bridge = 0.0
    reward_interval = float(os.getenv('REWARD_TICK_INTERVAL_SEC', '60') or '60')
    reward_interval = max(10.0, reward_interval)
    growth_bridge_interval = float(os.getenv('GROWTH_CONVERSION_BRIDGE_INTERVAL_SEC', '60') or '60')
    growth_bridge_interval = max(10.0, growth_bridge_interval)
''',
        '''    last_ux_guard = 0.0
    last_reward = 0.0
    last_growth_conversion_bridge = 0.0
    last_payment_reconciliation_retry = 0.0
    reward_interval = float(os.getenv('REWARD_TICK_INTERVAL_SEC', '60') or '60')
    reward_interval = max(10.0, reward_interval)
    growth_bridge_interval = float(os.getenv('GROWTH_CONVERSION_BRIDGE_INTERVAL_SEC', '60') or '60')
    growth_bridge_interval = max(10.0, growth_bridge_interval)
    payment_retry_interval = float(os.getenv('PAYMENT_RETRY_INTERVAL_SEC', '30') or '30')
    payment_retry_interval = max(5.0, payment_retry_interval)
''',
        label="payment retry scheduler state",
    )
    text = replace_once(
        text,
        '''        if now_m - last_growth_conversion_bridge >= growth_bridge_interval:
            last_growth_conversion_bridge = now_m
            await _run_protected_tick("GrowthConversionBridge.tick", _run_growth_conversion_bridge_tick)

        # UX guard (best-effort)
''',
        '''        if now_m - last_growth_conversion_bridge >= growth_bridge_interval:
            last_growth_conversion_bridge = now_m
            await _run_protected_tick("GrowthConversionBridge.tick", _run_growth_conversion_bridge_tick)

        # Durable provider-verified payment side-effect retries.
        now_m = time.monotonic()
        if now_m - last_payment_reconciliation_retry >= payment_retry_interval:
            last_payment_reconciliation_retry = now_m
            await _run_protected_tick(
                "PaymentReconciliationRetry.tick",
                _run_payment_reconciliation_retry_tick,
            )

        # UX guard (best-effort)
''',
        label="payment retry scheduler call",
    )
    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
