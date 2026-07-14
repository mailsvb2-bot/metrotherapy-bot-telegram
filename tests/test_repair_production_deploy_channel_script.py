from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "repair_production_deploy_channel.sh"


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
    assert "gh secret set GITHUB_WEBHOOK_SECRET" in text
    assert "WEBHOOK_SECRET=$WEBHOOK_SECRET" not in text
    assert "echo $WEBHOOK_SECRET" not in text
    assert "printf '%s' \"$WEBHOOK_SECRET\" | gh secret set" in text
    assert "SERVER_LOCAL_BRANCH_COUNT=" in text
    assert "GITHUB_BRANCH_COUNT=" in text
