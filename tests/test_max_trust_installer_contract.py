from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_max_trust.sh"
WORKER = ROOT / "scripts" / "run_deploy_worker.sh"


def test_max_trust_installer_shell_is_valid() -> None:
    result = subprocess.run(
        ["bash", "-n", str(INSTALLER)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


def test_max_trust_installer_pins_official_sources_and_hashes() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert "https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt" in source
    assert "https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt" in source
    assert "936a43fea6e8e525bcc0f81acd9c3d21b4fc4b9b68acea7906d698005afc6504" in source
    assert "f0ae589f36774f29ef3648f7984b08d42fcce6f1ffeeb6236d773daeb2744ea6" in source
    assert "CN=Russian Trusted Root CA" in source
    assert "CN=Russian Trusted Sub CA" in source


def test_max_trust_installer_never_disables_tls_verification() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert "--proto '=https'" in source
    assert "--tlsv1.2" in source
    assert "platform-api2.max.ru/me" in source
    assert "--insecure" not in source
    assert " -k " not in source
    assert "CERT_NONE" not in source
    assert "check_hostname = False" not in source


def test_deploy_worker_installs_trust_before_deploy_and_commits_marker_after_success() -> None:
    source = WORKER.read_text(encoding="utf-8")

    installer_call = source.index('scripts/install_max_trust.sh')
    deploy_call = source.index('/usr/bin/bash "$DEPLOY_SH"')
    marker_touch = source.index('touch "$MAX_TRUST_MIGRATION_MARKER"')

    assert "max-mincifry-trust-v1.applied" in source
    assert installer_call < deploy_call < marker_touch
    assert 'PYTHON_BIN="$PYTHON"' in source
