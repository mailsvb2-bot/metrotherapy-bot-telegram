from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OUTER = ROOT / "scripts" / "yookassa_refund_drill_guard.py"
INNER = ROOT / "scripts" / "yookassa_refund_drill_guard_inner.py"


def _load_path(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_outer():
    return _load_path(OUTER, "yookassa_refund_outer_contract")


def _load_inner():
    return _load_path(INNER, "yookassa_refund_inner_contract")


def test_outer_guard_imports_from_arbitrary_working_directory(tmp_path: Path) -> None:
    code = (
        "import runpy; "
        f"runpy.run_path({str(OUTER)!r}, run_name='yookassa_outer_import_contract')"
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


def test_outer_guard_reduces_uncaught_exception_to_class_only(tmp_path: Path, monkeypatch) -> None:
    module = _load_outer()
    inner = tmp_path / "failing_inner.py"
    inner.write_text('raise ValueError("secret provider payload must not escape")\n', encoding="utf-8")
    status_file = tmp_path / "guard.status"
    monkeypatch.setenv("YOOKASSA_REFUND_INNER_GUARD", str(inner))
    monkeypatch.setenv("YOOKASSA_GUARD_STATUS_FILE", str(status_file))
    monkeypatch.setenv("DEPLOY_TRIGGER_SHA", "a" * 40)

    assert module.main() == 2
    saved = status_file.read_text(encoding="utf-8").strip()
    assert saved == (
        "[ops-live-proof-result] trigger=aaaaaaaaaaaa status=blocked "
        "yookassa_refund=blocked reason=unexpected_bootstrap_ValueError"
    )
    assert "secret provider payload" not in saved


def test_outer_guard_reduces_unclassified_system_exit(tmp_path: Path, monkeypatch) -> None:
    module = _load_outer()
    inner = tmp_path / "exiting_inner.py"
    inner.write_text("raise SystemExit(7)\n", encoding="utf-8")
    status_file = tmp_path / "guard.status"
    monkeypatch.setenv("YOOKASSA_REFUND_INNER_GUARD", str(inner))
    monkeypatch.setenv("YOOKASSA_GUARD_STATUS_FILE", str(status_file))
    monkeypatch.setenv("DEPLOY_TRIGGER_SHA", "b" * 40)

    assert module.main() == 2
    saved = status_file.read_text(encoding="utf-8").strip()
    assert saved.endswith("reason=unexpected_guard_exit_SystemExit")
    assert "7" not in saved


def test_outer_guard_preserves_valid_cached_blocker(tmp_path: Path, monkeypatch) -> None:
    module = _load_outer()
    inner = tmp_path / "cached_inner.py"
    status_file = tmp_path / "guard.status"
    trigger = "c" * 40
    message = (
        "[ops-live-proof-result] trigger=cccccccccccc status=blocked "
        "yookassa_refund=blocked reason=provider_test_shop_required"
    )
    inner.write_text(
        "from pathlib import Path\n"
        "import os\n"
        f"Path(os.environ['YOOKASSA_GUARD_STATUS_FILE']).write_text({(message + chr(10))!r}, encoding='utf-8')\n"
        "raise SystemExit(2)\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("YOOKASSA_REFUND_INNER_GUARD", str(inner))
    monkeypatch.setenv("YOOKASSA_GUARD_STATUS_FILE", str(status_file))
    monkeypatch.setenv("DEPLOY_TRIGGER_SHA", trigger)

    assert module.main() == 2
    assert status_file.read_text(encoding="utf-8").strip() == message


def test_inner_guard_reduces_unexpected_exception_to_stage_and_class() -> None:
    module = _load_inner()
    impl = module._load_impl()

    def fail() -> None:
        raise ValueError("secret provider payload must not escape")

    guarded = module._guard(impl, "full", fail)
    with pytest.raises(impl.RefundDrillError, match="^unexpected_full_ValueError$"):
        guarded()


def test_inner_guard_reduces_system_exit_to_stage_without_exit_code() -> None:
    module = _load_inner()
    impl = module._load_impl()

    def fail() -> None:
        raise SystemExit(77)

    guarded = module._guard(impl, "environment", fail)
    with pytest.raises(impl.RefundDrillError, match="^unexpected_environment_SystemExit$") as exc_info:
        guarded()
    assert "77" not in str(exc_info.value)


def test_operation_guards_reduce_payment_create_system_exit_to_operation() -> None:
    module = _load_inner()
    impl = module._load_impl()

    def fail(*, user_id: int, package_id: str):
        del user_id, package_id
        raise SystemExit(91)

    impl._create_test_payment = fail
    module._install_operation_guards(impl)

    with pytest.raises(impl.RefundDrillError, match="^unexpected_payment_create_SystemExit$") as exc_info:
        impl._create_test_payment(user_id=-9138001, package_id="practice_antistress_60")
    assert "91" not in str(exc_info.value)


def test_operation_guard_wiring_covers_provider_and_db_boundaries() -> None:
    source = INNER.read_text(encoding="utf-8")

    for stage in (
        "amount",
        "payment_create",
        "payment_webhook",
        "db_count",
        "refund_create",
        "full_refund_assert",
        "reserve_token",
        "refund_state",
    ):
        assert f'"{stage}"' in source
    assert "_install_operation_guards(impl)" in source


def test_inner_guard_classifies_import_failure_without_exception_text(tmp_path: Path, monkeypatch) -> None:
    module = _load_inner()
    status_file = tmp_path / "guard.status"
    monkeypatch.setenv("YOOKASSA_GUARD_STATUS_FILE", str(status_file))
    monkeypatch.setenv("DEPLOY_TRIGGER_SHA", "d" * 40)

    def fail_import():
        raise ValueError("secret provider payload must not escape")

    monkeypatch.setattr(module, "_load_impl", fail_import)

    assert module.main() == 2
    saved = status_file.read_text(encoding="utf-8").strip()
    assert saved.endswith("reason=unexpected_import_ValueError")
    assert "secret provider payload" not in saved


def test_inner_blocked_result_survives_publisher_failure(tmp_path: Path, monkeypatch) -> None:
    module = _load_inner()
    impl = module._load_impl()
    status_file = tmp_path / "guard.status"
    impl.TRIGGER_SHA = "e" * 40
    monkeypatch.setenv("YOOKASSA_GUARD_STATUS_FILE", str(status_file))
    monkeypatch.setattr(module, "_load_impl", lambda: impl)
    monkeypatch.setattr(
        impl,
        "run_drill",
        lambda: (_ for _ in ()).throw(impl.RefundDrillError("provider_test_shop_required")),
    )
    monkeypatch.setattr(
        impl,
        "_publish_result",
        lambda _message: (_ for _ in ()).throw(impl.RefundDrillError("result_publish_race")),
    )

    assert module.main() == 2
    saved = status_file.read_text(encoding="utf-8").strip()
    assert saved.endswith("provider_test_shop_required")
    assert "result_publish_race" not in saved


def test_outer_guard_uses_stdlib_only_import_surface() -> None:
    source = OUTER.read_text(encoding="utf-8")

    assert "services." not in source
    assert "scripts.yookassa_refund_drill" not in source
    assert "runpy.run_path" in source
    assert "except BaseException" in source
