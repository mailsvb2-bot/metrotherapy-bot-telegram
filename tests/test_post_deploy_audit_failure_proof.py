from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "run_deploy_worker_observed.sh"


def test_observed_worker_post_deploy_fallback_has_valid_bash_syntax() -> None:
    bash = shutil.which("bash")
    assert bash is not None
    completed = subprocess.run(
        [bash, "-n", str(WORKER)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_production_gate_substage_classifier_reports_last_allowlisted_header() -> None:
    bash = shutil.which("bash")
    assert bash is not None
    source = WORKER.read_text(encoding="utf-8")
    start = source.index("classify_production_gate_substage()")
    end = source.index("\npublish_deploy_failure_result()", start)
    function = source[start:end]
    completed = subprocess.run(
        [bash, "-c", function + "\nclassify_production_gate_substage"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        input=(
            "==> prod validator\n"
            "validator output that must never be published\n"
            "==> postgres restore drill\n"
            "sensitive-looking raw failure text\n"
        ),
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "postgres_restore_drill"
    assert "sensitive" not in completed.stdout


def test_production_gate_substage_classifier_fails_closed_to_unknown() -> None:
    bash = shutil.which("bash")
    assert bash is not None
    source = WORKER.read_text(encoding="utf-8")
    start = source.index("classify_production_gate_substage()")
    end = source.index("\npublish_deploy_failure_result()", start)
    function = source[start:end]
    completed = subprocess.run(
        [bash, "-c", function + "\nclassify_production_gate_substage"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        input="unrecognized provider payload or arbitrary log text\n",
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "unknown"


def test_production_gate_failure_publishes_only_allowlisted_substage() -> None:
    source = WORKER.read_text(encoding="utf-8")
    function = source[
        source.index("publish_deploy_failure_result()") : source.index("publish_post_deploy_failure_result()")
    ]
    message_line = next(
        line
        for line in function.splitlines()
        if "gate_substage=$gate_substage" in line and "[deploy-failure-result]" in line
    )

    assert 'stage="production_gate_failed"' in function
    assert 'classify_production_gate_substage' in function
    assert "gate_substage=$gate_substage" in message_line
    assert "$segment" not in message_line
    assert "$matched" not in message_line
    assert "stderr" not in message_line.lower()
    assert "traceback" not in message_line.lower()


def test_post_deploy_audit_failure_publishes_only_allowlisted_fields() -> None:
    source = WORKER.read_text(encoding="utf-8")

    assert "publish_post_deploy_failure_result()" in source
    assert 'case "$audit" in' in source
    assert 'live_proof|yookassa_refund)' in source
    assert '*) audit="unknown" ;;' in source
    assert (
        'message="[ops-live-proof-result] trigger=${TRIGGER_SHA:0:12} '
        'status=error audit=$audit code=$audit_code"'
    ) in source
    assert "stderr" not in source[source.index("publish_post_deploy_failure_result()") :]
    assert "traceback" not in source.lower()[source.index("publish_post_deploy_failure_result()").__int__() :]


def test_specialized_result_wins_over_generic_fallback() -> None:
    source = WORKER.read_text(encoding="utf-8")
    function = source[
        source.index("publish_post_deploy_failure_result()") : source.index("run_post_deploy_audit()")
    ]

    fetch = function.index("git -C \"$APP_DIR\" fetch origin main")
    specialized = function.index('[ops-live-proof-result]')
    generic = function.index('message="[ops-live-proof-result]')

    assert fetch < specialized < generic
    assert 'trigger=${TRIGGER_SHA:0:12}' in function
    assert "return 0" in function[specialized:generic]


def test_post_deploy_runner_captures_exit_before_fallback() -> None:
    source = WORKER.read_text(encoding="utf-8")
    function = source[
        source.index("run_post_deploy_audit()") : source.index("set +e\n/usr/bin/bash \"$INNER_WORKER\"")
    ]

    disable_errexit = function.index("set +e")
    invocation = function.index('"$python_bin" "$runner"')
    capture = function.index('audit_code="$?"')
    restore_errexit = function.index("set -e", capture)
    fallback = function.index('publish_post_deploy_failure_result "$audit" "$audit_code"')

    assert disable_errexit < invocation < capture < restore_errexit < fallback
    assert 'return "$audit_code"' in function
    assert 'python_bin="$YOOKASSA_REFUND_PYTHON"' in function


def test_yookassa_audit_runs_only_after_successful_inner_deploy() -> None:
    source = WORKER.read_text(encoding="utf-8")

    inner = source.index('/usr/bin/bash "$INNER_WORKER"')
    inner_failure = source.index('if [ "$INNER_CODE" -ne 0 ]')
    yookassa_marker = source.index('[yookassa-refund-live-proof-request]')
    yookassa_call = source.index(
        'run_post_deploy_audit "yookassa_refund" "$YOOKASSA_REFUND_DRILL" 42'
    )
    completion = source.index("deploy worker completed trigger=%s")

    assert inner < inner_failure < yookassa_marker < yookassa_call < completion


def test_result_commits_remain_non_recursive_deploy_triggers() -> None:
    source = WORKER.read_text(encoding="utf-8")
    guard = source[: source.index("classify_production_gate_substage()")]

    assert '[ops-live-proof-result]' in guard
    assert '[deploy-failure-result]' in guard
    assert "exit 0" in guard
