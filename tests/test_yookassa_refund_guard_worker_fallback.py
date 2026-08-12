from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "run_deploy_worker_observed.sh"


def _cached_reader_function() -> str:
    source = WORKER.read_text(encoding="utf-8")
    start = source.index("read_cached_yookassa_result()")
    end = source.index("\npublish_deploy_failure_result()", start)
    return source[start:end]


def _run_reader(status_file: Path, trigger: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(
        [bash, "-c", _cached_reader_function() + "\nread_cached_yookassa_result"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            "PATH": "/usr/bin:/bin",
            "YOOKASSA_GUARD_STATUS_FILE": str(status_file),
            "TRIGGER_SHA": trigger,
        },
    )


def test_worker_accepts_only_current_trigger_redacted_cache(tmp_path: Path) -> None:
    status = tmp_path / "guard.status"
    trigger = "a" * 40
    message = (
        "[ops-live-proof-result] trigger=aaaaaaaaaaaa status=blocked "
        "yookassa_refund=blocked reason=unexpected_full_ValueError"
    )
    status.write_text(message + "\n", encoding="utf-8")

    completed = _run_reader(status, trigger)

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == message


def test_worker_rejects_wrong_trigger_or_unsafe_cache(tmp_path: Path) -> None:
    status = tmp_path / "guard.status"
    trigger = "a" * 40
    status.write_text(
        "[ops-live-proof-result] trigger=bbbbbbbbbbbb status=blocked "
        "yookassa_refund=blocked reason=provider_error\n",
        encoding="utf-8",
    )
    wrong_trigger = _run_reader(status, trigger)
    assert wrong_trigger.returncode != 0
    assert wrong_trigger.stdout == ""

    status.write_text(
        "[ops-live-proof-result] trigger=aaaaaaaaaaaa status=blocked "
        "yookassa_refund=blocked reason=unsafe?payload\n",
        encoding="utf-8",
    )
    unsafe = _run_reader(status, trigger)
    assert unsafe.returncode != 0
    assert unsafe.stdout == ""


def test_worker_clears_stale_cache_before_refund_audit() -> None:
    source = WORKER.read_text(encoding="utf-8")
    function = source[
        source.index("run_post_deploy_audit()") : source.index("set +e\n/usr/bin/bash \"$INNER_WORKER\"")
    ]

    choose_python = function.index('python_bin="$YOOKASSA_REFUND_PYTHON"')
    clear = function.index('rm -f "$YOOKASSA_GUARD_STATUS_FILE"')
    export = function.index('export YOOKASSA_GUARD_STATUS_FILE')
    invoke = function.index('"$python_bin" "$runner"')

    assert choose_python < clear < export < invoke


def test_worker_prefers_validated_cached_result_over_generic_code() -> None:
    source = WORKER.read_text(encoding="utf-8")
    function = source[
        source.index("publish_post_deploy_failure_result()") : source.index("run_post_deploy_audit()")
    ]

    read_cache = function.index("read_cached_yookassa_result")
    cached_assignment = function.index('message="$cached_message"')
    generic = function.index('message="[ops-live-proof-result] trigger=${TRIGGER_SHA:0:12} status=error')

    assert read_cache < cached_assignment < generic
