from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "production_live_proofs.py"
OBSERVED_WORKER = ROOT / "scripts" / "run_deploy_worker_observed.sh"


def _load_module():
    spec = importlib.util.spec_from_file_location("production_live_proofs_contract", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_replace_env_value_replaces_duplicates_without_touching_comments() -> None:
    module = _load_module()
    source = "# DEMO_KEY=comment\nA=1\nDEMO_KEY=old\nDEMO_KEY=older\n"

    result = module.replace_env_value(source, "DEMO_KEY", "new value")

    assert "# DEMO_KEY=comment" in result
    assert result.count("DEMO_KEY=") == 2
    assert "DEMO_KEY='new value'" in result
    assert "=old" not in result
    assert "=older" not in result


def test_replace_env_value_appends_missing_value() -> None:
    module = _load_module()

    result = module.replace_env_value("A=1\n", "DEMO_KEY", "abc")

    assert result == "A=1\nDEMO_KEY=abc\n"


def test_safe_fragment_preserves_audit_marker_and_removes_log_injection() -> None:
    module = _load_module()

    result = module._safe_fragment(
        "[ops-live-proof-result] status=ok\nunsafe? injected\rnext",
        limit=200,
    )

    assert result.startswith("[ops-live-proof-result] status=ok")
    assert "\n" not in result
    assert "\r" not in result
    assert "?" not in result


def test_observed_worker_runs_live_proofs_only_after_successful_inner_deploy() -> None:
    source = OBSERVED_WORKER.read_text(encoding="utf-8")

    inner_call = source.index('/usr/bin/bash "$INNER_WORKER"')
    proof_call = source.index('/usr/bin/python3 "$LIVE_PROOF_RUNNER"')

    assert inner_call < proof_call
    assert "[vk-confirmation-sync-request]" in source
    assert "[rollback-live-proof-request]" in source
    assert "[ops-live-proof-result]" in source
    assert "_ops-live-proof-result_" in source
