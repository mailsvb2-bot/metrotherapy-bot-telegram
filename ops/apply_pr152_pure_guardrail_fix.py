from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "scripts" / "regression_gate.py"

OLD = '''    GateStep(
        "hermetic production contract validation",
        (sys.executable, "scripts/validate_project.py"),
        HERMETIC_PROD_VALIDATOR_ENV,
    ),
'''

NEW = '''    GateStep(
        "hermetic production contract validation",
        (
            sys.executable,
            "-c",
            "from services.validators.prod import validate_prod_guardrails; "
            "validate_prod_guardrails(strict=True); "
            "print('HERMETIC_PROD_CONTRACT_OK')",
        ),
        HERMETIC_PROD_VALIDATOR_ENV,
    ),
'''


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    count = text.count(OLD)
    if count == 0 and NEW in text:
        return 0
    if count != 1:
        raise SystemExit(f"expected exactly one production gate command target, got {count}")
    TARGET.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
