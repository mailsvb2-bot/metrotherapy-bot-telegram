from __future__ import annotations

# This contract also protects the one-main cleanup path used after repair PRs.
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_production_deploy_channel.sh"
RECOVERY_WORKFLOW = ROOT / ".github" / "workflows" / "production-deploy-recovery.yml"


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
