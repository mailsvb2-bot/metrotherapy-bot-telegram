from __future__ import annotations

from pathlib import Path

from scripts import regression_gate, regression_gate_ci

ROOT = Path(__file__).resolve().parents[1]


def test_ci_adapter_delegates_exactly_one_full_pytest_step() -> None:
    original_names = [step.name for step in regression_gate.STEPS]
    delegated_names = [step.name for step in regression_gate_ci.delegated_steps()]

    assert original_names.count("full pytest regression gate") == 1
    assert "full pytest regression gate" not in delegated_names
    assert len(delegated_names) == len(original_names) - 1
    assert [name for name in original_names if name != "full pytest regression gate"] == delegated_names


def test_ci_workflow_runs_full_suite_only_through_coverage_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "python scripts/regression_gate_ci.py" in workflow
    assert workflow.count("python scripts/coverage_gate.py") == 1
    assert "python scripts/regression_gate.py 2>&1" not in workflow
