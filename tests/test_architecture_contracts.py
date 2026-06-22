from __future__ import annotations

import importlib


def test_fsm_states_have_single_identity():
    compat = importlib.import_module('handlers.states')
    canonical = importlib.import_module('handlers.text_input_parts.states')
    assert compat.InputState is canonical.InputState
    assert compat.AdminInputState is canonical.AdminInputState
    assert compat.MarketingCopyState is canonical.MarketingCopyState
    assert compat.RolesInputState is canonical.RolesInputState


def test_db_core_does_not_import_writer_eagerly():
    import services.db.core as core
    assert not hasattr(core, '_enqueue')


def test_architecture_contract_validator_imports():
    mod = importlib.import_module('services.validators.architecture')
    assert callable(mod.validate_architecture_contracts)

def test_engine_job_dispatch_contract_guardrail():
    from services.validators.architecture import validate_engine_job_dispatch_contract

    validate_engine_job_dispatch_contract(strict=True)


def test_engine_demo_send_has_no_unreachable_demo_sent_log():
    from pathlib import Path

    source = Path("core/engine.py").read_text(encoding="utf-8")
    assert 'return\n\n        await asyncio.to_thread(log_event, user_id, "demo_sent"' not in source


def test_decision_core_engine_job_policy_allows_registry_jobs_and_denies_unknown():
    from core.ai.decision_core import DecisionCore

    core = DecisionCore.instance()
    allowed = core.decide({"intent": "engine_job_execute", "job_type": "demo_send", "user_id": 0})
    denied = core.decide({"intent": "engine_job_execute", "job_type": "ghost_job", "user_id": 0})

    assert allowed.payload["type"] == "job_execution_allowed"
    assert allowed.meta["policy"] == "engine_job_registry_v1"
    assert denied.payload["type"] == "job_execution_denied"
    assert denied.payload["reason"] == "unknown_job_type"

