from __future__ import annotations


"""
Scenario engine v8.
Non-breaking orchestrator layer. Does not alter existing UX.
"""

from dataclasses import dataclass
from typing import Optional

@dataclass
class ScenarioSession:
    session_id: str
    user_id: int
    scope: str
    track_key: Optional[str] = None


def create_session(user_id: int, scope: str, track_key: Optional[str] = None) -> ScenarioSession:
    return ScenarioSession(session_id=f"{user_id}:{scope}", user_id=user_id, scope=scope, track_key=track_key)


def next_step(_: ScenarioSession) -> str:
    return "ok"