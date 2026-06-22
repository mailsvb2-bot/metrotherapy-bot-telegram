from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
import time
import secrets

WorldState = Dict[str, Any]


@dataclass(frozen=True)
class DecisionToken:
    """Capability token: issued only by DecisionCore; validated at action boundary."""
    decision_id: str
    issued_at: float
    ttl_sec: int
    signature: str
    nonce: str = field(default_factory=lambda: secrets.token_hex(8))

    def is_expired(self, now: Optional[float] = None) -> bool:
        n = time.time() if now is None else float(now)
        return n > (self.issued_at + float(self.ttl_sec))


@dataclass(frozen=True)
class Decision:
    decision_id: str
    payload: Dict[str, Any]
    token: DecisionToken
    meta: Dict[str, Any] = field(default_factory=dict)
