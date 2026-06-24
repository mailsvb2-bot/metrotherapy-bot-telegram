from __future__ import annotations

from core.ai.decision_core import ALLOWED_ENGINE_JOB_TYPES
from core.engine import engine


def test_decision_core_job_allowlist_matches_engine_registry():
    assert set(engine._job_handlers().keys()) == set(ALLOWED_ENGINE_JOB_TYPES)
