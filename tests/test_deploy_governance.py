from pathlib import Path

from scripts.check_deploy_governance import deploy_governance_problems


def test_production_deploy_is_read_only_toward_github() -> None:
    assert deploy_governance_problems() == []


def test_deploy_governance_rejects_server_pushes(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy.sh"
    deploy.write_text(
        """
require_single_local_main_branch() { :; }
audit_server_branch_topology_if_requested() { git ls-remote --heads origin; git push origin main; }
audit_server_branch_topology_if_requested
SERVER_BRANCH_AUDIT_OK
""".strip(),
        encoding="utf-8",
    )

    problems = deploy_governance_problems(deploy)

    assert "production deploy must not push to GitHub" in problems


def test_deploy_governance_requires_read_only_evidence(tmp_path: Path) -> None:
    deploy = tmp_path / "deploy.sh"
    deploy.write_text("require_single_local_main_branch() { :; }\n", encoding="utf-8")

    problems = deploy_governance_problems(deploy)

    assert "read-only audit function missing" in problems
    assert "remote branch topology read check missing" in problems
    assert "read-only audit evidence marker missing" in problems
