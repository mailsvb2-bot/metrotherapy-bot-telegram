from __future__ import annotations

"""GitHub Actions regression contour with pytest delegated to coverage_gate.

Local and release invocations of scripts/regression_gate.py keep their canonical
full pytest step. The CI workflow runs the same suite exactly once under line
and branch coverage, so this adapter removes only that duplicated step.
"""

from scripts import regression_gate

_FULL_PYTEST_STEP = "full pytest regression gate"


def delegated_steps() -> tuple[regression_gate.GateStep, ...]:
    steps = tuple(step for step in regression_gate.STEPS if step.name != _FULL_PYTEST_STEP)
    if len(steps) != len(regression_gate.STEPS) - 1:
        raise RuntimeError("canonical full pytest regression step not found exactly once")
    return steps


def main() -> int:
    original_steps = regression_gate.STEPS
    regression_gate.STEPS = delegated_steps()
    try:
        return regression_gate.main()
    finally:
        regression_gate.STEPS = original_steps


if __name__ == "__main__":
    raise SystemExit(main())
