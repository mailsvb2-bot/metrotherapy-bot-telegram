from __future__ import annotations

from pathlib import Path

from scripts.check_deploy_governance import deploy_governance_problems
from scripts.check_release_hygiene import ALLOWED_ROOT_FILES

ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_deploy_wrapper_has_no_mutable_runtime_logic() -> None:
    wrapper = _text("deploy.sh")

    assert "METROTHERAPY_ENV_FILE" in wrapper
    assert 'if [ ! -f "$ENV_FILE" ]' in wrapper
    assert "export PYTHONDONTWRITEBYTECODE=1" in wrapper
    assert "scripts/check_remote_main_topology.sh" in wrapper
    assert "scripts/immutable_deploy.sh" in wrapper
    assert wrapper.index('if [ ! -f "$ENV_FILE" ]') < wrapper.index(
        "scripts/check_remote_main_topology.sh"
    )
    assert wrapper.index("export PYTHONDONTWRITEBYTECODE=1") < wrapper.index(
        "scripts/immutable_deploy.sh"
    )
    assert ".venv/bin/python" not in wrapper
    assert "git reset" not in wrapper
    assert "pip install" not in wrapper


def test_immutable_deploy_governance_is_closed() -> None:
    assert deploy_governance_problems() == []


def test_builder_creates_per_sha_hash_locked_release() -> None:
    builder = _text("scripts/build_immutable_release.sh")

    assert 'FINAL_DIR="$RELEASES_DIR/$SHA"' in builder
    assert 'git -C "$SOURCE_DIR" archive --format=tar "$SHA"' in builder
    assert '"$SYSTEM_PYTHON" -m venv "$BUILD_DIR/.venv"' in builder
    assert "--no-compile --require-hashes" in builder
    assert 'tree-digest "$BUILD_DIR"' in builder
    assert 'chmod 0755 "$BUILD_DIR"' in builder
    assert "not path.is_symlink()" in builder
    assert 'mv "$BUILD_DIR" "$FINAL_DIR"' in builder
    assert '"$SYSTEM_PYTHON" "$MANAGER" validate "$FINAL_DIR"' in builder
    assert 'rm -rf "$BUILD_DIR"' in builder


def test_builder_separates_shared_audio_from_release_code() -> None:
    builder = _text("scripts/build_immutable_release.sh")

    assert 'SHARED_AUDIO_DIR="${SHARED_AUDIO_DIR:-$(dirname "$RUNTIME_ROOT")/audio}"' in builder
    assert 'cp -a "$SOURCE_DIR/audio/." "$SHARED_AUDIO_DIR/"' in builder
    assert 'ln -s "$SHARED_AUDIO_DIR" "$BUILD_DIR/audio"' in builder
    assert 'chmod -R a+rX "$SHARED_AUDIO_DIR"' in builder


def test_immutable_deploy_orders_switch_gate_proof_and_marker() -> None:
    deploy = _text("scripts/immutable_deploy.sh")
    pipeline = deploy.index("sync_deploy_webhook_service\n")
    expand = deploy.index('validate_candidate_and_expand_schema "$CANDIDATE_DIR"', pipeline)
    compatibility = deploy.index(
        'verify_previous_release_on_expanded_schema "$CURRENT_RELEASE_DIR"',
        expand,
    )
    switch = deploy.index('if [ "$NEW_SHA" != "$OLD_RUNTIME_SHA" ]', compatibility)
    restart = deploy.index("restart_runtime_and_wait", switch)
    gate = deploy.index('"$CURRENT_LINK/scripts/production_gate.py"', restart)
    proof = deploy.index('"$SYSTEM_PYTHON" "$RELEASE_MANAGER" write-proof', gate)
    marker = deploy.index('record_successful_deployed_sha "$NEW_SHA"', proof)

    assert expand < compatibility < switch < restart < gate < proof < marker
    assert "--require-hashes" not in deploy  # dependency install belongs only to builder
    assert "post_deploy_verify.py --skip-pytest" not in deploy


def test_rollback_switches_symlink_before_restart() -> None:
    deploy = _text("scripts/immutable_deploy.sh")
    rollback = deploy.index('"$SYSTEM_PYTHON" "$RELEASE_MANAGER" rollback')
    restart = deploy.index('systemctl restart "$SERVICE_NAME"', rollback)

    assert rollback < restart
    assert "git reset --hard" not in deploy
    assert 'PREVIOUS_LINK="${METRO_PREVIOUS_RELEASE_LINK:-$RUNTIME_ROOT/previous}"' in deploy


def test_previous_release_is_checked_after_expand_migrations() -> None:
    deploy = _text("scripts/immutable_deploy.sh")
    pipeline = deploy.index("sync_deploy_webhook_service\n")
    expand = deploy.index('validate_candidate_and_expand_schema "$CANDIDATE_DIR"', pipeline)
    compatibility = deploy.index(
        'verify_previous_release_on_expanded_schema "$CURRENT_RELEASE_DIR"',
        expand,
    )
    switch = deploy.index('if [ "$NEW_SHA" != "$OLD_RUNTIME_SHA" ]', compatibility)

    assert expand < compatibility < switch
    assert "PREVIOUS_RELEASE_EXPANDED_SCHEMA_OK" in deploy


def test_deployed_sha_is_not_written_by_preflight_or_rollback() -> None:
    deploy = _text("scripts/immutable_deploy.sh")

    assert deploy.count('record_successful_deployed_sha "$NEW_SHA"') == 1
    call = deploy.index('record_successful_deployed_sha "$NEW_SHA"')
    assert deploy.index("PRODUCTION_GATE_OK") < call
    rollback_region = deploy[deploy.index("rollback() {") : deploy.index("cleanup_old_releases() {")]
    assert "record_successful_deployed_sha" not in rollback_region


def test_systemd_template_executes_only_current_release() -> None:
    service = _text("deploy/metrotherapy.service")

    assert "WorkingDirectory=/var/lib/metrotherapy/runtime/current" in service
    assert (
        "ExecStart=/var/lib/metrotherapy/runtime/current/.venv/bin/python "
        "/var/lib/metrotherapy/runtime/current/main.py"
    ) in service
    assert "PYTHONDONTWRITEBYTECODE=1" in service
    assert "METRO_DB_ENGINE=postgres" in service
    assert "METRO_DB_ENGINE=sqlite" not in service
    assert "/opt/metrotherapy/.venv" not in service
    assert "/root/metrotherapy/.venv" not in service


def test_release_marker_is_a_canonical_hygiene_artifact() -> None:
    assert ".release.json" in ALLOWED_ROOT_FILES


def test_remote_topology_gate_is_read_only_and_exact() -> None:
    topology = _text("scripts/check_remote_main_topology.sh")

    assert "ls-remote --heads origin" in topology
    assert 'branches" != "main"' in topology
    assert "REMOTE_TOPOLOGY_OK" in topology
    assert "git push" not in topology
    assert "deleteRef" not in topology
