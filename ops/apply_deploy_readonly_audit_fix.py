from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy.sh"

OLD_BLOCK = r'''publish_server_branch_audit_if_requested() {
  local request_message
  local local_branch_list
  local local_branch_count
  local local_branch_csv
  local remote_branch_list
  local remote_branch_count
  local remote_branch_csv
  local audit_message

  request_message="$(git log -1 --pretty=%B)"
  case "$request_message" in
    *"[server-branch-audit-request]"*) ;;
    *) return 0 ;;
  esac

  echo "=== explicit server branch audit requested ==="
  local_branch_list="$(git for-each-ref --format='%(refname:short)' refs/heads | sort)"
  local_branch_count="$(printf '%s\n' "$local_branch_list" | sed '/^$/d' | wc -l | tr -d ' ')"
  local_branch_csv="$(printf '%s\n' "$local_branch_list" | sed '/^$/d' | paste -sd, -)"

  remote_branch_list="$(
    "$TIMEOUT_BIN" --signal=TERM --kill-after=10s "$GIT_NETWORK_TIMEOUT_SECONDS" \
      git ls-remote --heads origin \
      | awk '{ref=$2; sub("^refs/heads/", "", ref); print ref}' \
      | sort
  )"
  remote_branch_count="$(printf '%s\n' "$remote_branch_list" | sed '/^$/d' | wc -l | tr -d ' ')"
  remote_branch_csv="$(printf '%s\n' "$remote_branch_list" | sed '/^$/d' | paste -sd, -)"

  if [ "$local_branch_count" != "1" ] || [ "$local_branch_csv" != "main" ]; then
    echo "ERROR: server audit expected local_count=1 local_branches=main"
    echo "ERROR: local_count=$local_branch_count local_branches=$local_branch_csv"
    exit 12
  fi
  if [ "$remote_branch_count" != "1" ] || [ "$remote_branch_csv" != "main" ]; then
    echo "ERROR: server audit expected remote_count=1 remote_branches=main"
    echo "ERROR: remote_count=$remote_branch_count remote_branches=$remote_branch_csv"
    exit 13
  fi

  audit_message="[server-branch-audit-result] local_count=$local_branch_count local_branches=$local_branch_csv remote_count=$remote_branch_count remote_branches=$remote_branch_csv"
  git -c user.name="Metrotherapy Deploy Audit" \
      -c user.email="deploy-audit@metrotherapy.local" \
      commit --allow-empty -m "$audit_message"
  run_bounded "$GIT_NETWORK_TIMEOUT_SECONDS" \
    "publish server branch audit result" \
    git push origin main
  echo "=== $audit_message ==="
}'''

NEW_BLOCK = r'''audit_server_branch_topology_if_requested() {
  local request_message
  local local_branch_list
  local local_branch_count
  local local_branch_csv
  local remote_branch_list
  local remote_branch_count
  local remote_branch_csv

  request_message="$(git log -1 --pretty=%B)"
  case "$request_message" in
    *"[server-branch-audit-request]"*) ;;
    *) return 0 ;;
  esac

  echo "=== explicit read-only server branch audit requested ==="
  local_branch_list="$(git for-each-ref --format='%(refname:short)' refs/heads | sort)"
  local_branch_count="$(printf '%s\n' "$local_branch_list" | sed '/^$/d' | wc -l | tr -d ' ')"
  local_branch_csv="$(printf '%s\n' "$local_branch_list" | sed '/^$/d' | paste -sd, -)"

  remote_branch_list="$(
    "$TIMEOUT_BIN" --signal=TERM --kill-after=10s "$GIT_NETWORK_TIMEOUT_SECONDS" \
      git ls-remote --heads origin \
      | awk '{ref=$2; sub("^refs/heads/", "", ref); print ref}' \
      | sort
  )"
  remote_branch_count="$(printf '%s\n' "$remote_branch_list" | sed '/^$/d' | wc -l | tr -d ' ')"
  remote_branch_csv="$(printf '%s\n' "$remote_branch_list" | sed '/^$/d' | paste -sd, -)"

  if [ "$local_branch_count" != "1" ] || [ "$local_branch_csv" != "main" ]; then
    echo "ERROR: server audit expected local_count=1 local_branches=main"
    echo "ERROR: local_count=$local_branch_count local_branches=$local_branch_csv"
    exit 12
  fi
  if [ "$remote_branch_count" != "1" ] || [ "$remote_branch_csv" != "main" ]; then
    echo "ERROR: server audit expected remote_count=1 remote_branches=main"
    echo "ERROR: remote_count=$remote_branch_count remote_branches=$remote_branch_csv"
    exit 13
  fi

  echo "=== SERVER_BRANCH_AUDIT_OK local_count=$local_branch_count local_branches=$local_branch_csv remote_count=$remote_branch_count remote_branches=$remote_branch_csv ==="
}'''


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count == 0 and new in text:
        return text
    if count != 1:
        raise SystemExit(f"expected exactly one {label} target, got {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = DEPLOY.read_text(encoding="utf-8")
    text = replace_once(text, OLD_BLOCK, NEW_BLOCK, label="audit function")
    text = replace_once(
        text,
        "publish_server_branch_audit_if_requested\n",
        "audit_server_branch_topology_if_requested\n",
        label="audit call",
    )
    DEPLOY.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
