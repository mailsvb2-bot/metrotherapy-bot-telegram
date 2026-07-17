from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_PATH = ROOT / "deploy.sh"


def deploy_governance_problems(path: Path = DEPLOY_PATH) -> list[str]:
    if not path.is_file():
        return [f"missing deploy script: {path}"]

    text = path.read_text(encoding="utf-8")
    problems: list[str] = []
    forbidden = {
        "git push": "production deploy must not push to GitHub",
        "commit --allow-empty": "production deploy must not manufacture audit commits",
        "publish_server_branch_audit_if_requested": "legacy write-oriented audit function remains",
        "Metrotherapy Deploy Audit": "legacy deploy write identity remains",
    }
    for needle, message in forbidden.items():
        if needle in text:
            problems.append(message)

    required = {
        "audit_server_branch_topology_if_requested()": "read-only audit function missing",
        "audit_server_branch_topology_if_requested\n": "read-only audit function is not invoked",
        "git ls-remote --heads origin": "remote branch topology read check missing",
        "SERVER_BRANCH_AUDIT_OK": "read-only audit evidence marker missing",
        "require_single_local_main_branch": "local single-main enforcement missing",
    }
    for needle, message in required.items():
        if needle not in text:
            problems.append(message)

    if text.count("audit_server_branch_topology_if_requested\n") != 1:
        problems.append("read-only audit invocation must appear exactly once")
    return problems


def main() -> int:
    problems = deploy_governance_problems()
    if problems:
        print("DEPLOY_GOVERNANCE_FAILED")
        for problem in problems:
            print(problem)
        return 1
    print("DEPLOY_GOVERNANCE_OK read_only_remote_audit=1 github_write_credentials_required=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
