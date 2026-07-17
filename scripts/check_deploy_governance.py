from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_PATH = ROOT / "deploy.sh"
IMMUTABLE_DEPLOY_PATH = ROOT / "scripts" / "immutable_deploy.sh"
REMOTE_TOPOLOGY_PATH = ROOT / "scripts" / "check_remote_main_topology.sh"


def _read(path: Path, label: str, problems: list[str]) -> str:
    if not path.is_file():
        problems.append(f"missing {label}: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def deploy_governance_problems(
    path: Path = DEPLOY_PATH,
    immutable_path: Path = IMMUTABLE_DEPLOY_PATH,
    remote_path: Path = REMOTE_TOPOLOGY_PATH,
) -> list[str]:
    problems: list[str] = []
    wrapper = _read(path, "deploy wrapper", problems)
    immutable = _read(immutable_path, "immutable deploy script", problems)
    remote = _read(remote_path, "remote topology gate", problems)
    combined = "\n".join((wrapper, immutable, remote))

    forbidden = {
        "git push": "production deploy must not push to GitHub",
        "commit --allow-empty": "production deploy must not manufacture audit commits",
        "git reset --hard": "runtime rollback must not mutate the source checkout",
        "publish_server_branch_audit_if_requested": "legacy write-oriented audit function remains",
        "Metrotherapy Deploy Audit": "legacy deploy write identity remains",
        '"$SOURCE_DIR/.venv': "production deploy must not use the shared source virtualenv",
        "post_deploy_verify.py --skip-pytest": "ordinary deploy must not bypass the strict production gate",
    }
    for needle, message in forbidden.items():
        if needle in combined:
            problems.append(message)

    wrapper_required = {
        "scripts/check_remote_main_topology.sh": "deploy wrapper does not run remote topology audit",
        "scripts/immutable_deploy.sh": "deploy wrapper does not delegate to immutable deployment",
    }
    for needle, message in wrapper_required.items():
        if needle not in wrapper:
            problems.append(message)

    remote_required = {
        "git -C \"$SOURCE_DIR\" ls-remote --heads origin": "read-only remote branch audit is missing",
        "REMOTE_TOPOLOGY_OK": "remote topology evidence marker is missing",
        'branches" != "main"': "remote topology does not require exactly main",
    }
    for needle, message in remote_required.items():
        if needle not in remote:
            problems.append(message)

    immutable_required = {
        "build_immutable_release.sh": "per-SHA release builder is not invoked",
        "immutable_release.py": "atomic release manager is not invoked",
        'RUNTIME_ROOT="${METRO_RUNTIME_ROOT:-/var/lib/metrotherapy/runtime}"': "runtime release root is missing",
        'CURRENT_LINK="${METRO_CURRENT_RELEASE_LINK:-$RUNTIME_ROOT/current}"': "current release link is missing",
        'PREVIOUS_LINK="${METRO_PREVIOUS_RELEASE_LINK:-$RUNTIME_ROOT/previous}"': "previous release link is missing",
        "previous release compatibility on expanded schema": "previous-code expanded-schema proof is missing",
        '"$SYSTEM_PYTHON" "$RELEASE_MANAGER" rollback': "atomic symlink rollback is missing",
        '"$CURRENT_LINK/scripts/production_gate.py"': "mandatory production gate is missing",
        '"$SYSTEM_PYTHON" "$RELEASE_MANAGER" write-proof': "deployment proof write is missing",
        'record_successful_deployed_sha "$NEW_SHA"': "successful deployed SHA marker is missing",
        "PRODUCTION_GATE_OK": "production gate evidence marker is missing",
        "tree_sha256": "immutable tree evidence is not referenced by deploy tooling",
    }
    for needle, message in immutable_required.items():
        if needle not in immutable:
            problems.append(message)

    production_gate_pos = immutable.find('"$CURRENT_LINK/scripts/production_gate.py"')
    proof_pos = immutable.find('"$SYSTEM_PYTHON" "$RELEASE_MANAGER" write-proof')
    marker_pos = immutable.find('record_successful_deployed_sha "$NEW_SHA"')
    if min(production_gate_pos, proof_pos, marker_pos) < 0 or not (
        production_gate_pos < proof_pos < marker_pos
    ):
        problems.append("deploy order must be production gate, deployment proof, then deployed SHA marker")

    rollback_pos = immutable.find('"$SYSTEM_PYTHON" "$RELEASE_MANAGER" rollback')
    restart_in_rollback = immutable.find('systemctl restart "$SERVICE_NAME"', rollback_pos)
    if rollback_pos < 0 or restart_in_rollback < rollback_pos:
        problems.append("rollback must switch the release link before restarting the service")

    return problems


def main() -> int:
    problems = deploy_governance_problems()
    if problems:
        print("DEPLOY_GOVERNANCE_FAILED")
        for problem in problems:
            print(problem)
        return 1
    print(
        "DEPLOY_GOVERNANCE_OK immutable_releases=1 atomic_rollback=1 "
        "mandatory_restore_gate=1 github_write_credentials_required=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
