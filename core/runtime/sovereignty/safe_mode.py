from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import time


@dataclass
class SafeModeState:
    active: bool = False
    reason: str = ""
    since_ts: float = 0.0
    last_incident_id: Optional[str] = None

    def enable(self, reason: str, incident_id: Optional[str] = None) -> None:
        self.active = True
        self.reason = str(reason)
        self.since_ts = time.time()
        self.last_incident_id = incident_id

    def clear(self) -> None:
        self.active = False
        self.reason = ""
        self.since_ts = 0.0
        self.last_incident_id = None


SAFE_MODE = SafeModeState()
