from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_github_deploy_webhook_service.sh"
UNIT = ROOT / "deploy" / "github-deploy-webhook.service"


def test_webhook_service_installer_has_valid_bash_syntax() -> None:
    bash = shutil.which("bash")
    assert bash is not None
    completed = subprocess.run(
        [bash, "-n", str(INSTALLER)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


def test_webhook_service_unit_uses_current_production_paths() -> None:
    text = UNIT.read_text(encoding="utf-8")

    assert "WorkingDirectory=/root/metrotherapy" in text
    assert "EnvironmentFile=/etc/metrotherapy/github-deploy-webhook.env" in text
    assert "ExecStart=/root/metrotherapy/.venv/bin/python /root/deploy_webhook.py" in text
    assert "Restart=always" in text
    assert "/root/bot/" not in text


def test_installer_waits_for_real_listener_and_prints_diagnostics() -> None:
    text = INSTALLER.read_text(encoding="utf-8")

    assert "http://127.0.0.1:9001/github-deploy" in text
    assert "for attempt in $(seq 1 30)" in text
    assert "journalctl -u \"$HOOK_SERVICE\" -n 120" in text
    assert "systemctl show \"$HOOK_SERVICE\" -p ExecStart" in text
    assert "WEBHOOK_SERVICE_OK" in text
