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
