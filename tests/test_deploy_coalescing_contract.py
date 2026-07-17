from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_LAUNCHER = ROOT / "deploy.sh"
IMMUTABLE_DEPLOY = ROOT / "scripts" / "immutable_deploy.sh"
RELEASE_MANAGER = ROOT / "scripts" / "immutable_release.py"
RELEASE_BUILDER = ROOT / "scripts" / "build_immutable_release.sh"
REMOTE_TOPOLOGY = ROOT / "scripts" / "check_remote_main_topology.sh"
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


def _prepare_deploy_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run("git", "init", "-b", "main", cwd=repo)
    _run("git", "config", "user.name", "Deploy Contract", cwd=repo)
    _run("git", "config", "user.email", "deploy-contract@example.test", cwd=repo)

    scripts = repo / "scripts"
    scripts.mkdir()
    shutil.copy2(RELEASE_MANAGER, scripts / RELEASE_MANAGER.name)
    shutil.copy2(RELEASE_BUILDER, scripts / RELEASE_BUILDER.name)
    _run("git", "add", "scripts", cwd=repo)
    _run("git", "commit", "-m", "fixture release tooling", cwd=repo)

    copied_script = tmp_path / "immutable_deploy.sh"
    shutil.copy2(IMMUTABLE_DEPLOY, copied_script)
    return repo, copied_script


def test_deploy_launcher_and_immutable_pipeline_have_valid_bash_syntax() -> None:
    _assert_bash_syntax(DEPLOY_LAUNCHER)
    _assert_bash_syntax(IMMUTABLE_DEPLOY)
    _assert_bash_syntax(RELEASE_BUILDER)
    _assert_bash_syntax(REMOTE_TOPOLOGY)


def test_deploy_launcher_delegates_topology_then_immutable_pipeline() -> None:
    source = DEPLOY_LAUNCHER.read_text(encoding="utf-8")
    topology = source.index('bash "$SOURCE_DIR/scripts/check_remote_main_topology.sh" "$SOURCE_DIR"')
    immutable = source.index('exec bash "$SOURCE_DIR/scripts/immutable_deploy.sh" "$@"')
    assert topology < immutable
    assert "git reset --hard" not in source
    assert "pip install" not in source


def test_deploy_worker_has_valid_bash_syntax() -> None:
    _assert_bash_syntax(WORKER)


def test_stale_deploy_recovery_has_valid_bash_syntax() -> None:
    assert STALE_RECOVERY.is_file()
    _assert_bash_syntax(STALE_RECOVERY)


def test_older_trigger_is_coalesced_before_release_build_or_switch(tmp_path) -> None:
    bash = shutil.which("bash")
    git = shutil.which("git")
    assert bash is not None
    assert git is not None

    repo, copied_script = _prepare_deploy_fixture(tmp_path)
    trigger_sha = _commit(repo, "one.txt", "one\n", "one")
    deployed_sha = _commit(repo, "two.txt", "two\n", "two")
    _run("git", "remote", "add", "origin", str(repo), cwd=repo)

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    marker = state_dir / "deployed_sha"
    marker.write_text(f"{deployed_sha}\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"

    env = os.environ.copy()
    env.update(
        {
            "APP_DIR": str(repo),
            "DEPLOY_TRIGGER_SHA": trigger_sha,
            "DEPLOY_STATE_DIR": str(state_dir),
            "DEPLOYED_SHA_FILE": str(marker),
            "METRO_RUNTIME_ROOT": str(runtime_root),
            "METRO_RELEASES_DIR": str(runtime_root / "releases"),
            "METRO_CURRENT_RELEASE_LINK": str(runtime_root / "current"),
            "METRO_PREVIOUS_RELEASE_LINK": str(runtime_root / "previous"),
            "METRO_IMMUTABLE_SYSTEMD_OVERRIDE": str(tmp_path / "immutable-release.conf"),
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
    assert f"deploy coalesced trigger={trigger_sha} deployed={deployed_sha}" in completed.stdout
    assert "build immutable release" not in completed.stdout
    assert "mandatory production backup" not in completed.stdout
    assert not runtime_root.exists()


def test_dirty_checkout_never_trusts_successful_deploy_marker(tmp_path) -> None:
    bash = shutil.which("bash")
    git = shutil.which("git")
    assert bash is not None
    assert git is not None

    repo, copied_script = _prepare_deploy_fixture(tmp_path)
    trigger_sha = _commit(repo, "one.txt", "one\n", "one")
    deployed_sha = _commit(repo, "two.txt", "two\n", "two")
    _run("git", "remote", "add", "origin", str(repo), cwd=repo)
    (repo / "two.txt").write_text("manually changed\n", encoding="utf-8")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    marker = state_dir / "deployed_sha"
    marker.write_text(f"{deployed_sha}\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "APP_DIR": str(repo),
            "DEPLOY_TRIGGER_SHA": trigger_sha,
            "DEPLOY_STATE_DIR": str(state_dir),
            "DEPLOYED_SHA_FILE": str(marker),
            "METRO_RUNTIME_ROOT": str(tmp_path / "runtime"),
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

    assert completed.returncode == 10
    assert "IMMUTABLE_DEPLOY_FAILED dirty source worktree" in completed.stderr
    assert "deploy coalesced" not in completed.stdout


def test_coalescing_requires_clean_main_checkout_and_success_marker() -> None:
    source = IMMUTABLE_DEPLOY.read_text(encoding="utf-8")
    dirty_check = source.index('git status --porcelain')
    checkout = source.index("git checkout main")
    fetch = source.index('run_bounded "$GIT_NETWORK_TIMEOUT_SECONDS" "fetch origin"')
    marker_read = source.index('read_recorded_sha 2>/dev/null')
    ancestor_check = source.index('git merge-base --is-ancestor "$TRIGGER_SHA" "$recorded_sha"')
    runtime_creation = source.index('mkdir -p "$RUNTIME_ROOT" "$RELEASES_DIR" "$DEPLOY_STATE_DIR"')
    assert dirty_check < checkout < fetch < marker_read < ancestor_check < runtime_creation
    assert "IMMUTABLE_DEPLOY_FAILED dirty source worktree" in source
    assert "deploy coalesced trigger=" in source


def test_success_marker_is_atomic_and_written_after_proof_and_gate() -> None:
    source = IMMUTABLE_DEPLOY.read_text(encoding="utf-8")
    marker_function = source.index("record_successful_deployed_sha()")
    production_gate = source.rindex('"$CURRENT_LINK/scripts/production_gate.py"')
    proof = source.rindex('"$RELEASE_MANAGER" write-proof')
    record_call = source.rindex('record_successful_deployed_sha "$NEW_SHA"')
    cleanup = source.rindex("cleanup_old_releases")
    trap_removed = source.rindex("trap - ERR TERM INT HUP")
    assert 'mktemp "$DEPLOY_STATE_DIR/deployed_sha.XXXXXX"' in source
    assert 'mv -f "$temp" "$DEPLOYED_SHA_FILE"' in source
    assert marker_function < production_gate < proof < record_call < cleanup < trap_removed


def test_coalescing_keeps_provider_audits_after_deploy_returns() -> None:
    deploy_source = IMMUTABLE_DEPLOY.read_text(encoding="utf-8")
    worker_source = WORKER.read_text(encoding="utf-8")
    assert 'git merge-base --is-ancestor "$TRIGGER_SHA" "$recorded_sha"' in deploy_source
    deploy_call = worker_source.index('/usr/bin/bash "$DEPLOY_SH"')
    stars_audit = worker_source.rindex("publish_stars_provider_audit_if_requested")
    max_audit = worker_source.rindex("publish_max_provider_audit_if_requested")
    vk_audit = worker_source.rindex("publish_vk_provider_audit_if_requested")
    assert deploy_call < stars_audit < max_audit < vk_audit


def test_waiting_workers_never_truncate_active_lock_metadata() -> None:
    source = WORKER.read_text(encoding="utf-8")
    open_lock = source.index('exec 9<>"$LOCK_FILE"')
    acquire_lock = source.index('"$FLOCK_BIN" -w "$LOCK_WAIT_SECONDS" 9')
    acquisition_epoch = source.index('LOCK_ACQUIRED_EPOCH="$(date +%s)"')
    replace_metadata = source.index(': > "$LOCK_FILE"', acquisition_epoch)
    write_metadata = source.index('"$LOCK_METADATA_VERSION"', replace_metadata)
    clear_metadata = source.rindex(': > "$LOCK_FILE"')
    assert 'exec 9>"$LOCK_FILE"' not in source
    assert open_lock < acquire_lock < acquisition_epoch < replace_metadata < write_metadata
    assert write_metadata < clear_metadata
    assert 'LOCK_METADATA_VERSION="v1"' in source
    assert '"$$"' in source[replace_metadata:write_metadata + 200]
    assert '"$LOCK_ACQUIRED_EPOCH"' in source[replace_metadata:write_metadata + 300]


def test_every_long_immutable_deploy_phase_has_a_hard_deadline() -> None:
    source = IMMUTABLE_DEPLOY.read_text(encoding="utf-8")
    assert 'TIMEOUT_BIN="${TIMEOUT_BIN:-/usr/bin/timeout}"' in source
    assert '"$TIMEOUT_BIN" --signal=TERM --kill-after=30s "$seconds" "$@"' in source
    for timeout_name in (
        "GIT_NETWORK_TIMEOUT_SECONDS",
        "WEBHOOK_RESTART_TIMEOUT_SECONDS",
        "RELEASE_BUILD_TIMEOUT_SECONDS",
        "VALIDATOR_TIMEOUT_SECONDS",
        "SCHEMA_COMPAT_TIMEOUT_SECONDS",
        "SERVICE_RESTART_TIMEOUT_SECONDS",
        "HEALTH_WAIT_SECONDS",
        "PRODUCTION_GATE_TIMEOUT_SECONDS",
    ):
        expected = f'"{timeout_name}:${timeout_name}"'
        assert expected in source
    assert 'run_bounded "$RELEASE_BUILD_TIMEOUT_SECONDS"' in source
    assert 'run_bounded "$VALIDATOR_TIMEOUT_SECONDS"' in source
    assert 'run_bounded "$SCHEMA_COMPAT_TIMEOUT_SECONDS"' in source
    assert 'run_bounded "$SERVICE_RESTART_TIMEOUT_SECONDS"' in source
    assert 'run_bounded "$PRODUCTION_GATE_TIMEOUT_SECONDS"' in source
    assert "IMMUTABLE_DEPLOY_FAILED command=" in source
    assert "trap 'rollback 143' TERM" in source
    assert "trap 'rollback 130' INT" in source
    assert "trap 'rollback 129' HUP" in source


def test_stale_recovery_uses_kernel_holder_and_lock_acquisition_age() -> None:
    source = STALE_RECOVERY.read_text(encoding="utf-8")
    assert 'LSLOCKS_BIN="${LSLOCKS_BIN:-/usr/bin/lslocks}"' in source
    assert 'STALE_AFTER_SECONDS="${STALE_AFTER_SECONDS:-3600}"' in source
    assert 'ALLOW_LEGACY_LOCK_METADATA="${ALLOW_LEGACY_LOCK_METADATA:-0}"' in source
    assert 'exec 8<>"$LOCK_FILE"' in source
    assert '"$FLOCK_BIN" -n 8' in source
    assert '"$LSLOCKS_BIN" --noheadings --raw --output PID,PATH' in source
    assert 'awk -v path="$LOCK_FILE"' in source
    assert 'holder_pid="$(printf' in source
    assert 'metadata_version" = "v1"' in source
    assert '"$metadata_pid" = "$holder_pid"' in source
    assert 'held_seconds="$((now_epoch - lock_acquired_epoch))"' in source
    legacy_gate = source.index('[ "$ALLOW_LEGACY_LOCK_METADATA" = "1" ]')
    legacy_process_age = source.index('"$PS_BIN" -o etimes= -p "$holder_pid"')
    assert legacy_gate < legacy_process_age


def test_stale_recovery_stops_only_the_exact_lock_holder_unit() -> None:
    source = STALE_RECOVERY.read_text(encoding="utf-8")
    assert 'kill -0 "$holder_pid"' in source
    assert 'grep -F -- "$WORKER_PATH"' in source
    assert '"$SYSTEMCTL_BIN" show "$unit" -p MainPID --value' in source
    assert 'if [ "$main_pid" = "$holder_pid" ]; then' in source
    assert '[ "$matching_count" -eq 1 ]' in source
    assert '"$TIMEOUT_BIN"' in source
    assert '"$SYSTEMCTL_BIN" stop "$matching_unit"' in source
    assert '"$FLOCK_BIN" -w "$LOCK_RELEASE_WAIT_SECONDS" 8' in source
    assert "STALE_DEPLOY_RECOVERY_OK" in source
    assert "pkill" not in source
    assert "killall" not in source
    assert '"$SYSTEMCTL_BIN" stop "$UNIT_PATTERN"' not in source
