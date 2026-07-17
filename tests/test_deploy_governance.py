from pathlib import Path

from scripts.check_deploy_governance import deploy_governance_problems


def test_production_deploy_is_read_only_toward_github() -> None:
    assert deploy_governance_problems() == []


def test_deploy_governance_rejects_server_pushes(tmp_path: Path) -> None:
    wrapper = tmp_path / "deploy.sh"
    immutable = tmp_path / "immutable_deploy.sh"
    remote = tmp_path / "remote.sh"
    wrapper.write_text(
        "bash scripts/check_remote_main_topology.sh\nexec bash scripts/immutable_deploy.sh\n",
        encoding="utf-8",
    )
    immutable.write_text("git push origin main\n", encoding="utf-8")
    remote.write_text("git ls-remote --heads origin\nREMOTE_TOPOLOGY_OK\n", encoding="utf-8")

    problems = deploy_governance_problems(wrapper, immutable, remote)

    assert "production deploy must not push to GitHub" in problems


def test_deploy_governance_requires_remote_read_only_evidence(tmp_path: Path) -> None:
    wrapper = tmp_path / "deploy.sh"
    immutable = tmp_path / "immutable_deploy.sh"
    remote = tmp_path / "remote.sh"
    wrapper.write_text(
        "bash scripts/check_remote_main_topology.sh\nexec bash scripts/immutable_deploy.sh\n",
        encoding="utf-8",
    )
    immutable.write_text("", encoding="utf-8")
    remote.write_text("echo no-audit\n", encoding="utf-8")

    problems = deploy_governance_problems(wrapper, immutable, remote)

    assert "read-only remote branch audit is missing" in problems
    assert "remote topology evidence marker is missing" in problems
    assert "remote topology does not require exactly main" in problems


def test_deploy_governance_rejects_mutable_rollback(tmp_path: Path) -> None:
    wrapper = tmp_path / "deploy.sh"
    immutable = tmp_path / "immutable_deploy.sh"
    remote = tmp_path / "remote.sh"
    wrapper.write_text(
        "bash scripts/check_remote_main_topology.sh\nexec bash scripts/immutable_deploy.sh\n",
        encoding="utf-8",
    )
    immutable.write_text("git reset --hard old-sha\n", encoding="utf-8")
    remote.write_text(
        'git -C "$SOURCE_DIR" ls-remote --heads origin\n'
        'if [ "$branches" != "main" ]; then exit 1; fi\n'
        "REMOTE_TOPOLOGY_OK\n",
        encoding="utf-8",
    )

    problems = deploy_governance_problems(wrapper, immutable, remote)

    assert "runtime rollback must not mutate the source checkout" in problems
