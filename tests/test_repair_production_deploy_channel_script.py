from __future__ import annotations

# This contract also protects the one-main cleanup path used after repair PRs.
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_production_deploy_channel.sh"
RECOVERY_WORKFLOW = ROOT / ".github" / "workflows" / "production-deploy-recovery.yml"
TOPOLOGY_WORKFLOW = ROOT / ".github" / "workflows" / "production-server-topology-probe.yml"
CLEANUP_WORKFLOW = ROOT / ".github" / "workflows" / "single-main-topology.yml"


def test_production_deploy_repair_script_has_valid_bash_syntax() -> None:
    bash = shutil.which("bash")
    assert bash is not None
    assert SCRIPT.is_file()

    completed = subprocess.run(
        [bash, "-n", str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr


def test_production_deploy_repair_script_keeps_secrets_out_of_output() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "openssl rand -hex 48" in text
    assert 'ACTIONS_SECRET_NAME="${ACTIONS_SECRET_NAME:-METRO_DEPLOY_WEBHOOK_SECRET}"' in text
    assert 'gh secret set "$ACTIONS_SECRET_NAME"' in text
    assert "gh secret set GITHUB_WEBHOOK_SECRET" not in text
    assert "WEBHOOK_SECRET=$WEBHOOK_SECRET" not in text
    assert "echo $WEBHOOK_SECRET" not in text
    assert "printf '%s' \"$WEBHOOK_SECRET\" | gh secret set" in text
    assert "SERVER_LOCAL_BRANCH_COUNT=" in text
    assert "GITHUB_BRANCH_COUNT=" in text


def test_repair_rejects_reserved_github_actions_secret_prefix() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'GITHUB_*) fail "GitHub Actions secret names must not start with GITHUB_' in text
    assert "METRO_DEPLOY_WEBHOOK_SECRET" in text


def test_repair_delegates_webhook_service_ownership_to_canonical_installer() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert 'SERVICE_INSTALLER="$APP_DIR/scripts/install_github_deploy_webhook_service.sh"' in text
    assert 'bash "$SERVICE_INSTALLER"' in text
    assert "install canonical webhook runtime and systemd service" in text
    assert 'systemctl restart "$HOOK_SERVICE"' not in text
    assert 'cat > "$HOOK_DROPIN_FILE"' not in text
    assert "50-webhook-secret.conf" not in text


def test_recovery_workflow_reads_the_allowed_actions_secret_name() -> None:
    text = RECOVERY_WORKFLOW.read_text(encoding="utf-8")

    assert "secrets.METRO_DEPLOY_WEBHOOK_SECRET" in text
    assert "secrets.GITHUB_WEBHOOK_SECRET" not in text
    assert "[recover-production-deploy]" in text


def test_recovery_workflow_signs_the_same_trigger_bound_payload_it_posts() -> None:
    text = RECOVERY_WORKFLOW.read_text(encoding="utf-8")

    assert "TRIGGER_SHA: ${{ github.sha }}" in text
    assert r'\"after\":\"%s\"' in text
    assert '"$TRIGGER_SHA"' in text
    assert 'PAYLOAD="$payload" SECRET="$DEPLOY_WEBHOOK_SECRET"' in text
    assert '--data "$payload"' in text
    assert "GitHub recovery trigger SHA is invalid" in text
    assert "Signed trigger-bound production deploy queued" in text
    assert "payload='{\"ref\":\"refs/heads/main\"}'" not in text


def test_topology_probe_retries_transient_health_and_deploy_endpoint_failures() -> None:
    text = TOPOLOGY_WORKFLOW.read_text(encoding="utf-8")

    assert "for attempt in $(seq 1 24); do" in text
    assert "health_code=\"$(curl -sS -o /dev/null -w '%{http_code}'" in text
    assert '"$health" || true)' in text
    assert 'response="$(curl -fsS --max-time 5 "$endpoint" 2>/dev/null || true)"' in text
    assert '[ "$health_code" = "200" ]' in text
    assert "sleep 5" in text
    assert 'curl -fsS --max-time 5 "$health" >/dev/null' not in text
    assert "SERVER_HEALTH_CODE=" in text
    assert "Server health=${healthCode}" in text


def test_github_topology_cleanup_retries_eventually_consistent_branch_reads() -> None:
    text = CLEANUP_WORKFLOW.read_text(encoding="utf-8")

    delete_ref = text.index("github.rest.git.deleteRef")
    verification_loop = text.index("for (let attempt = 1; attempt <= 10; attempt += 1)")
    final_assertion = text.index("Expected exactly one GitHub branch named main")

    assert delete_ref < verification_loop < final_assertion
    assert "GITHUB_BRANCH_VERIFY_ATTEMPT=${attempt}" in text
    assert "setTimeout(resolve, attempt * 500)" in text
    assert "names.length === 1 && names[0] === 'main'" in text
