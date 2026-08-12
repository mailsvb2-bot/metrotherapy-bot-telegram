from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "yookassa_refund_drill_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("yookassa_refund_guard_contract", GUARD)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guard_imports_from_arbitrary_working_directory(tmp_path: Path) -> None:
    code = (
        "import runpy; "
        f"runpy.run_path({str(GUARD)!r}, run_name='yookassa_guard_import_contract')"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""


def test_guard_reduces_unexpected_exception_to_stage_and_class() -> None:
    module = _load_guard()

    def fail() -> None:
        raise ValueError("secret provider payload must not escape")

    guarded = module._guard("full", fail)
    with pytest.raises(module.impl.RefundDrillError, match="^unexpected_full_ValueError$"):
        guarded()
