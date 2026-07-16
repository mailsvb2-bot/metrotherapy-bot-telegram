from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "deploy.sh"
WORKER = ROOT / "scripts" / "run_deploy_worker.sh"
STALE_RECOVERY = ROOT / "scripts" / "recover_stale_deploy_worker.sh"


def _run(*command: str, cwd: Path) -> str:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return completed.stdout.strip()


def _commit(repo: Path, filename: str, content: str, message: str) -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    _run("git", "add", filename, cwd=repo)
    _run("git", "commit", "-m", message, cwd=repo)
    return _run("git", "rev-parse", "HEAD", cwd=repo)


def _assert_bash_syntax(path: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None

    completed = subprocess.run(
        [bash, "-n", str(path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr


def test_deploy_script_has_valid_bash_syntax() -> None:
    _assert_bash_syntax(DEPLOY_SCRIPT)


def test_stale_deploy_recovery_has_valid_bash_syntax() -> None:
    assert STALE_RECOVERY.is_file()
    _assert_bash_syntax(STALE_RECOVERY)


def test_older_trigger_is_coalesced_before_any_deploy_side_effect(tmp_path) -> None:
    bash = shutil.which("bash")
    git = shutil.which("git")
    assert bash is not None
    assert git is not None

    repo = tmp_path / "repo"
    repo.mkdir()
    _run("git", "init", "-b", "main", cwd=repo)
    _run("git", "config", "user.name", "Deploy Contract", cwd=repo)
    _run("git", "config", "user.email", "deploy-contract@example.test", cwd=repo)
    trigger_sha = _commit(repo, "one.txt", "one\n", "one")
    deployed_sha = _commit(repo, "two.txt", "two\n", "two")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    marker = state_dir / "deployed_sha"
    marker.write_text(f"{deployed_sha}\n", encoding="utf-8")

    copied_script = tmp_path / "deploy.sh"
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    source = source.replace(
        'APP_DIR="/root/metrotherapy"',
        f'APP_DIR="{repo}"',
        1,
    )
    copied_script.write_text(source, encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "DEPLOY_TRIGGER_SHA": trigger_sha,
            "DEPLOY_STATE_DIR": str(state_dir),
            "DEPLOYED_SHA_FILE": str(marker),
        }
    )
    completed = subprocess.run(
        [bash, str(copied_script)],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert (
        f"deploy coalesced: trigger={trigger_sha} "
        f"already covered by successful_sha={deployed_sha}"
    ) in completed.stdout
    assert "git status before" not in completed.stdout
    assert "sync deploy webhook service script" not in completed.stdout
    assert "restart service" not in completed.stdout


def test_success_marker_is_atomic_and_written_after_all_verification() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    marker_function = source.index("record_successful_deployed_sha()")
    post_deploy_verify = source.index('"$PYTHON" scripts/post_deploy_verify.py --skip-pytest')
    record_call = source.rindex('record_successful_deployed_sha "$NEW_SHA"')
    trap_removed = source.rindex("trap - ERR TERM INT HUP")

    assert 'mktemp "$DEPLOY_STATE_DIR/deployed_sha.XXXXXX"' in source
    assert 'mv -f "$tmp_file" "$DEPLOYED_SHA_FILE"' in source
    assert marker_function < post_deploy_verify < record_call < trap_removed


def test_coalescing_keeps_provider_audits_after_deploy_returns() -> None:
    deploy_source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    worker_source = WORKER.read_text(encoding="utf-8")

    assert 'git merge-base --is-ancestor "$TRIGGER_SHA" "$deployed_sha"' in deploy_source
    deploy_call = worker_source.index('/usr/bin/bash "$DEPLOY_SH"')
    stars_audit = worker_source.rindex("publish_stars_provider_audit_if_requested")
    max_audit = worker_source.rindex("publish_max_provider_audit_if_requested")
    vk_audit = worker_source.rindex("publish_vk_provider_audit_if_requested")

    assert deploy_call < stars_audit < max_audit < vk_audit


def test_every_long_production_deploy_phase_has_a_hard_deadline() -> None:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'TIMEOUT_BIN="${TIMEOUT_BIN:-/usr/bin/timeout}"' in source
    assert '"$TIMEOUT_BIN" --signal=TERM --kill-after=30s "$seconds" "$@"' in source
    assert 'PIP_INSTALL_TIMEOUT_SECONDS="${PIP_INSTALL_TIMEOUT_SECONDS:-600}"' in source
    assert 'VALIDATOR_TIMEOUT_SECONDS="${VALIDATOR_TIMEOUT_SECONDS:-300}"' in source
    assert 'POST_DEPLOY_VERIFY_TIMEOUT_SECONDS="${POST_DEPLOY_VERIFY_TIMEOUT_SECONDS:-600}"' in source
    assert 'run_bounded "$GIT_NETWORK_TIMEOUT_SECONDS"' in source
    assert 'run_bounded "$PIP_INSTALL_TIMEOUT_SECONDS"' in source
    assert 'run_bounded "$COMPILE_TIMEOUT_SECONDS"' in source
    assert 'run_bounded "$VALIDATOR_TIMEOUT_SECONDS"' in source
    assert 'run_bounded "$RUFF_TIMEOUT_SECONDS"' in source
    assert 'run_bounded "$SERVICE_RESTART_TIMEOUT_SECONDS"' in source
    assert 'run_bounded "$POST_DEPLOY_VERIFY_TIMEOUT_SECONDS"' in source
    assert "bounded command timed out" in source
    assert "trap 'rollback 143' TERM" in source
    assert "trap 'rollback 130' INT" in source
    assert "trap 'rollback 129' HUP" in source


def test_stale_recovery_stops_only_the_exact_lock_holder_unit() -> None:
    source = STALE_RECOVERY.read_text(encoding="utf-8")

    assert 'STALE_AFTER_SECONDS="${STALE_AFTER_SECONDS:-1200}"' in source
    assert 'holder_pid="$(sed -n' in source
    assert 'kill -0 "$holder_pid"' in source
    assert 'grep -F -- "$WORKER_PATH"' in source
    assert '"$FLOCK_BIN" -n 8' in source
    assert '"$SYSTEMCTL_BIN" show "$unit" -p MainPID --value' in source
    assert 'if [ "$main_pid" = "$holder_pid" ]; then' in source
    assert '[ "$matching_count" -eq 1 ]' in source
    assert '"$SYSTEMCTL_BIN" stop "$matching_unit"' in source
    assert '"$FLOCK_BIN" -w "$LOCK_RELEASE_WAIT_SECONDS" 8' in source
    assert "STALE_DEPLOY_RECOVERY_OK" in source
    assert "pkill" not in source
    assert "killall" not in source
    assert '"$SYSTEMCTL_BIN" stop "$UNIT_PATTERN"' not in source
