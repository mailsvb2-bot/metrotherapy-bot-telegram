from __future__ import annotations

import time
from dataclasses import dataclass

from core.runtime.sovereignty.safe_mode import SAFE_MODE


@dataclass
class SelfHealingEngine:
    cooldown_sec: int = 60
    _last_tick: float = 0.0

    def tick(self) -> None:
        now = time.time()
        if now - self._last_tick < 5:
            return
        self._last_tick = now

        if not SAFE_MODE.active:
            return

        # Minimal policy: never auto-clear ARCH_VIOLATION, but allow clearing INVALID_TOKEN/BYPASS after cooldown.
        reason = (SAFE_MODE.reason or "").upper()
        if "ARCH_VIOLATION" in reason:
            return
        if SAFE_MODE.since_ts and (now - SAFE_MODE.since_ts) >= self.cooldown_sec:
            SAFE_MODE.clear()
