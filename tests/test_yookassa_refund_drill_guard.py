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


def test_guard_records_only_exact_safe_result(tmp_path: Path, monkeypatch) -> None:
    module = _load_guard()
    status_file = tmp_path / "guard.status"
    monkeypatch.setenv("YOOKASSA_GUARD_STATUS_FILE", str(status_file))
    message = (
        "[ops-live-proof-result] trigger=abcdef012345 status=blocked "
        "yookassa_refund=blocked reason=unexpected_full_ValueError"
    )

    assert module._record_safe_result(message) is True
    assert status_file.read_text(encoding="utf-8") == message + "\n"
    assert status_file.stat().st_mode & 0o777 == 0o600

    status_file.unlink()
    assert module._record_safe_result(message + " secret?payload") is False
    assert not status_file.exists()


def test_blocked_result_survives_internal_publisher_failure(tmp_path: Path, monkeypatch) -> None:
    module = _load_guard()
    status_file = tmp_path / "guard.status"
    trigger = "a" * 40
    module.impl.TRIGGER_SHA = trigger
    monkeypatch.setenv("YOOKASSA_GUARD_STATUS_FILE", str(status_file))
    monkeypatch.setattr(
        module.impl,
        "run_drill",
        lambda: (_ for _ in ()).throw(module.impl.RefundDrillError("provider_test_shop_required")),
    )
    monkeypatch.setattr(
        module.impl,
        "_publish_result",
        lambda _message: (_ for _ in ()).throw(module.impl.RefundDrillError("result_publish_race")),
    )

    assert module.main() == 2
    saved = status_file.read_text(encoding="utf-8").strip()
    assert saved.startswith(
        "[ops-live-proof-result] trigger=aaaaaaaaaaaa status=blocked yookassa_refund=blocked reason="
    )
    assert saved.endswith("provider_test_shop_required")
    assert "result_publish_race" not in saved


def test_success_result_survives_internal_publisher_failure(tmp_path: Path, monkeypatch) -> None:
    module = _load_guard()
    status_file = tmp_path / "guard.status"
    module.impl.TRIGGER_SHA = "b" * 40
    expected = (
        "[ops-live-proof-result] trigger=bbbbbbbbbbbb status=ok yookassa_refund=ok "
        "provider_test=true provider_get_refund=ok"
    )
    monkeypatch.setenv("YOOKASSA_GUARD_STATUS_FILE", str(status_file))
    monkeypatch.setattr(module.impl, "run_drill", lambda: expected)
    monkeypatch.setattr(
        module.impl,
        "_publish_result",
        lambda _message: (_ for _ in ()).throw(module.impl.RefundDrillError("result_publish_race")),
    )

    assert module.main() == 3
    assert status_file.read_text(encoding="utf-8").strip() == expected
