#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/root/metrotherapy}"
REPO="${REPO:-mailsvb2-bot/metrotherapy-bot-telegram}"
HOOK_URL="${HOOK_URL:-https://metrotherapy-bot.metrotherapy.ru/github-deploy}"
HOOK_SERVICE="${HOOK_SERVICE:-github-deploy-webhook.service}"
HOOK_LOCAL_URL="${HOOK_LOCAL_URL:-http://127.0.0.1:9001/github-deploy}"
HOOK_SOURCE="$APP_DIR/ops/deploy_webhook.py"
HOOK_TARGET="/root/deploy_webhook.py"
HOOK_ENV_DIR="/etc/metrotherapy"
HOOK_ENV_FILE="$HOOK_ENV_DIR/github-deploy-webhook.env"
HOOK_UNIT_FILE="/etc/systemd/system/$HOOK_SERVICE"
SERVICE_INSTALLER="$APP_DIR/scripts/install_github_deploy_webhook_service.sh"
DEPLOY_LOCK="$APP_DIR/data/deploy/metrotherapy_deploy.lock"
DEPLOY_LOG="/var/log/metrotherapy_deploy.log"
PUBLIC_HEALTH_URL="https://metrotherapy-bot.metrotherapy.ru/healthz"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="/root/metrotherapy_deploy_repair_backup_$STAMP"

log() {
  printf '=== %s ===\n' "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"
}

if [ "$(id -u)" -ne 0 ]; then
  fail "run this script as root"
fi

for command_name in git gh python3 openssl curl systemctl install grep sed awk paste; do
  require_command "$command_name"
done

[ -d "$APP_DIR/.git" ] || fail "git repository not found: $APP_DIR"
[ -f "$HOOK_SOURCE" ] || fail "deploy webhook source not found: $HOOK_SOURCE"
[ -f "$APP_DIR/deploy.sh" ] || fail "deploy script not found: $APP_DIR/deploy.sh"
[ -f "$SERVICE_INSTALLER" ] || fail "canonical webhook service installer not found: $SERVICE_INSTALLER"

mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"
if [ -f "$HOOK_ENV_FILE" ]; then
  cp -a "$HOOK_ENV_FILE" "$BACKUP_DIR/github-deploy-webhook.env.before"
fi
if [ -f "$HOOK_UNIT_FILE" ]; then
  cp -a "$HOOK_UNIT_FILE" "$BACKUP_DIR/github-deploy-webhook.service.before"
fi
if [ -f "$HOOK_TARGET" ]; then
  cp -a "$HOOK_TARGET" "$BACKUP_DIR/deploy_webhook.py.before"
fi

cd "$APP_DIR"

log "verify clean production checkout"
if [ -n "$(git status --porcelain)" ]; then
  git status --short
  fail "production working tree is dirty; no files were overwritten"
fi

log "verify GitHub CLI authentication and repository administration"
gh auth status >/dev/null
gh api "repos/$REPO" --jq '.full_name + " permission=" + (.permissions.admin | tostring)' \
  | grep -F "permission=true" >/dev/null \
  || fail "the authenticated gh account does not have repository admin permission"

log "fast-forward production checkout to origin/main"
git fetch --prune origin
git checkout main
git merge --ff-only origin/main

log "remove all local server branches except main"
while IFS= read -r branch; do
  [ -n "$branch" ] || continue
  if [ "$branch" != "main" ]; then
    git branch -D "$branch"
  fi
done < <(git for-each-ref --format='%(refname:short)' refs/heads)
git remote prune origin

local_branches="$(git for-each-ref --format='%(refname:short)' refs/heads | sort)"
local_count="$(printf '%s\n' "$local_branches" | sed '/^$/d' | wc -l | tr -d ' ')"
[ "$local_count" = "1" ] && [ "$local_branches" = "main" ] \
  || fail "server must have exactly one local branch main; got count=$local_count branches=$local_branches"

remote_branches="$(git ls-remote --heads origin | awk '{ref=$2; sub("^refs/heads/", "", ref); print ref}' | sort)"
remote_count="$(printf '%s\n' "$remote_branches" | sed '/^$/d' | wc -l | tr -d ' ')"
[ "$remote_count" = "1" ] && [ "$remote_branches" = "main" ] \
  || fail "GitHub must have exactly one branch main; got count=$remote_count branches=$remote_branches"

log "rotate deploy webhook secret without printing it"
WEBHOOK_SECRET="$(openssl rand -hex 48)"
install -d -m 0750 "$HOOK_ENV_DIR"
temporary_env="$(mktemp "$HOOK_ENV_DIR/.github-deploy-webhook.env.XXXXXX")"
chmod 0600 "$temporary_env"
printf 'GITHUB_WEBHOOK_SECRET=%s\n' "$WEBHOOK_SECRET" > "$temporary_env"
install -m 0600 "$temporary_env" "$HOOK_ENV_FILE"
rm -f "$temporary_env"

log "install canonical webhook runtime and systemd service"
APP_DIR="$APP_DIR" \
HOOK_SERVICE="$HOOK_SERVICE" \
HOOK_LOCAL_URL="$HOOK_LOCAL_URL" \
  bash "$SERVICE_INSTALLER"

log "verify server and webhook share the rotated secret"
ping_payload='{}'
ping_signature="$(PAYLOAD="$ping_payload" SECRET="$WEBHOOK_SECRET" python3 - <<'PY'
import hashlib
import hmac
import os

payload = os.environ["PAYLOAD"].encode("utf-8")
secret = os.environ["SECRET"].encode("utf-8")
print("sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest())
PY
)"
ping_response="$(curl -fsS --max-time 10 \
  -X POST "$HOOK_LOCAL_URL" \
  -H 'Content-Type: application/json' \
  -H 'X-GitHub-Event: ping' \
  -H "X-Hub-Signature-256: $ping_signature" \
  --data "$ping_payload")"
[ "$ping_response" = "pong" ] || fail "local signed webhook ping failed: $ping_response"

log "store the same recovery secret in GitHub Actions"
printf '%s' "$WEBHOOK_SECRET" | gh secret set GITHUB_WEBHOOK_SECRET --repo "$REPO"
gh secret list --repo "$REPO" | awk '{print $1}' | grep -Fx 'GITHUB_WEBHOOK_SECRET' >/dev/null \
  || fail "GitHub Actions secret GITHUB_WEBHOOK_SECRET was not created"

log "create or update the canonical GitHub repository webhook"
hooks_file="$(mktemp)"
trap 'rm -f "$hooks_file"' EXIT
gh api "repos/$REPO/hooks?per_page=100" > "$hooks_file"
mapfile -t matching_hook_ids < <(HOOK_URL="$HOOK_URL" python3 - "$hooks_file" <<'PY'
import json
import os
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    hooks = json.load(handle)
url = os.environ["HOOK_URL"]
for hook in hooks:
    if str((hook.get("config") or {}).get("url") or "").rstrip("/") == url.rstrip("/"):
        print(hook["id"])
PY
)

hook_payload="$(HOOK_URL="$HOOK_URL" WEBHOOK_SECRET="$WEBHOOK_SECRET" python3 - <<'PY'
import json
import os

print(json.dumps({
    "name": "web",
    "active": True,
    "events": ["push"],
    "config": {
        "url": os.environ["HOOK_URL"],
        "content_type": "json",
        "insecure_ssl": "0",
        "secret": os.environ["WEBHOOK_SECRET"],
    },
}))
PY
)"

if [ "${#matching_hook_ids[@]}" -eq 0 ]; then
  hook_result="$(printf '%s' "$hook_payload" | gh api --method POST "repos/$REPO/hooks" --input -)"
  hook_id="$(printf '%s' "$hook_result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
else
  hook_id="${matching_hook_ids[0]}"
  printf '%s' "$hook_payload" | gh api --method PATCH "repos/$REPO/hooks/$hook_id" --input - >/dev/null
  if [ "${#matching_hook_ids[@]}" -gt 1 ]; then
    for duplicate_id in "${matching_hook_ids[@]:1}"; do
      gh api --method DELETE "repos/$REPO/hooks/$duplicate_id" >/dev/null
    done
  fi
fi

gh api --method POST "repos/$REPO/hooks/$hook_id/pings" >/dev/null
sleep 3
hook_state="$(gh api "repos/$REPO/hooks/$hook_id")"
printf '%s' "$hook_state" | python3 -c '
import json, sys
hook = json.load(sys.stdin)
assert hook.get("active") is True, hook
assert hook.get("events") == ["push"], hook
assert (hook.get("config") or {}).get("content_type") == "json", hook
print("HOOK_OK id=" + str(hook["id"]))
'

log "remove stale deploy lock only when no deploy process is running"
if pgrep -af '/root/metrotherapy/deploy\.sh|/usr/bin/bash .*deploy\.sh' >/dev/null 2>&1; then
  fail "a production deploy is already running; retry after it finishes"
fi
rm -f "$DEPLOY_LOCK"

log "verify public webhook topology endpoint"
public_topology="$(curl -fsS --max-time 15 "$HOOK_URL")"
printf '%s\n' "$public_topology"
printf '%s' "$public_topology" | grep -F 'local_branch_count=1' >/dev/null \
  || fail "public webhook does not report one local branch"
printf '%s' "$public_topology" | grep -F 'local_branches=main' >/dev/null \
  || fail "public webhook does not report local branch main"

log "send a real main push to prove automatic deployment"
git commit --allow-empty -m "ops: verify repaired production deploy channel"
test_sha="$(git rev-parse HEAD)"
git push origin main

log "wait for webhook-triggered deploy to reach the pushed SHA"
deploy_seen=0
for attempt in $(seq 1 120); do
  if [ -f "$DEPLOY_LOG" ] && grep -Fq "=== deployed sha: $test_sha ===" "$DEPLOY_LOG"; then
    deploy_seen=1
    break
  fi
  sleep 2
done
if [ "$deploy_seen" -ne 1 ]; then
  tail -n 160 "$DEPLOY_LOG" 2>/dev/null || true
  fail "automatic deploy did not confirm SHA $test_sha"
fi

log "final production verification"
systemctl is-active --quiet metrotherapy.service || fail "metrotherapy.service is not active"
curl -fsS --max-time 15 "$PUBLIC_HEALTH_URL" >/dev/null
final_local_branches="$(git for-each-ref --format='%(refname:short)' refs/heads | sort)"
final_local_count="$(printf '%s\n' "$final_local_branches" | sed '/^$/d' | wc -l | tr -d ' ')"
final_remote_branches="$(git ls-remote --heads origin | awk '{ref=$2; sub("^refs/heads/", "", ref); print ref}' | sort)"
final_remote_count="$(printf '%s\n' "$final_remote_branches" | sed '/^$/d' | wc -l | tr -d ' ')"

[ "$final_local_count" = "1" ] && [ "$final_local_branches" = "main" ] \
  || fail "final server topology is not 1/main"
[ "$final_remote_count" = "1" ] && [ "$final_remote_branches" = "main" ] \
  || fail "final GitHub topology is not 1/main"

cat <<EOF
REPAIR_OK
SERVER_LOCAL_BRANCH_COUNT=$final_local_count
SERVER_LOCAL_BRANCHES=$final_local_branches
GITHUB_BRANCH_COUNT=$final_remote_count
GITHUB_BRANCHES=$final_remote_branches
DEPLOYED_SHA=$test_sha
BACKUP_DIR=$BACKUP_DIR
EOF
