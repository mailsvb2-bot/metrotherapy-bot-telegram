#!/usr/bin/env bash
set -Eeuo pipefail

# Production topology contract: the server keeps one local branch named main.
APP_DIR="/root/metrotherapy"
SERVICE_NAME="metrotherapy.service"
DEPLOY_WEBHOOK_SERVICE="github-deploy-webhook.service"
DEPLOY_WEBHOOK_SOURCE="$APP_DIR/ops/deploy_webhook.py"
DEPLOY_WEBHOOK_TARGET="/root/deploy_webhook.py"
PYTHON="$APP_DIR/.venv/bin/python"
PIP="$APP_DIR/.venv/bin/pip"
ENV_FILE="/etc/metrotherapy/metrotherapy.env"
LOG_PREFIX="deploy"
LOCAL_HEALTH_URL="http://127.0.0.1:8082/healthz"
PUBLIC_HEALTH_URL="https://metrotherapy-bot.metrotherapy.ru/healthz"
HEALTH_WAIT_SECONDS="${HEALTH_WAIT_SECONDS:-60}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/metrotherapy/deploy-state}"
DEPLOYED_SHA_FILE="${DEPLOYED_SHA_FILE:-$DEPLOY_STATE_DIR/deployed_sha}"
TRIGGER_SHA="${DEPLOY_TRIGGER_SHA:-}"
ZERO_SHA="0000000000000000000000000000000000000000"

cd "$APP_DIR"

echo "=== $LOG_PREFIX started: $(date -Is) ==="
echo "=== app dir: $APP_DIR ==="

if [ -f "$ENV_FILE" ]; then
  echo "=== load env: $ENV_FILE ==="
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
else
  echo "WARNING: env file not found: $ENV_FILE"
fi

_is_truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes|on|webhook) return 0 ;;
    *) return 1 ;;
  esac
}

_is_valid_commit_sha() {
  local value="${1:-}"
  case "$value" in
    ''|*[!0-9a-f]*) return 1 ;;
  esac
  [ "${#value}" -eq 40 ] && [ "$value" != "$ZERO_SHA" ]
}

skip_if_trigger_already_deployed() {
  local deployed_sha=""

  if [ -z "$TRIGGER_SHA" ]; then
    echo "=== deploy coalescing disabled: no immutable trigger SHA ==="
    return 0
  fi
  if ! _is_valid_commit_sha "$TRIGGER_SHA"; then
    echo "ERROR: DEPLOY_TRIGGER_SHA is not one non-zero lowercase 40-character commit"
    exit 24
  fi
  if ! git cat-file -e "$TRIGGER_SHA^{commit}" 2>/dev/null; then
    echo "ERROR: deploy trigger commit is unavailable locally: $TRIGGER_SHA"
    exit 25
  fi
  if [ ! -f "$DEPLOYED_SHA_FILE" ]; then
    echo "=== deploy coalescing unavailable: no successful deployed SHA marker ==="
    return 0
  fi

  IFS= read -r deployed_sha < "$DEPLOYED_SHA_FILE" || true
  if ! _is_valid_commit_sha "$deployed_sha"; then
    echo "WARNING: ignoring invalid successful deployed SHA marker"
    return 0
  fi
  if ! git cat-file -e "$deployed_sha^{commit}" 2>/dev/null; then
    echo "WARNING: ignoring unavailable successful deployed SHA marker: $deployed_sha"
    return 0
  fi

  if git merge-base --is-ancestor "$TRIGGER_SHA" "$deployed_sha"; then
    echo "=== deploy coalesced: trigger=$TRIGGER_SHA already covered by successful_sha=$deployed_sha ==="
    return 10
  fi
  echo "=== deploy required: trigger=$TRIGGER_SHA successful_sha=$deployed_sha ==="
  return 0
}

record_successful_deployed_sha() {
  local deployed_sha="$1"
  local tmp_file

  if ! _is_valid_commit_sha "$deployed_sha"; then
    echo "ERROR: refusing to record invalid successful deployed SHA"
    return 26
  fi
  mkdir -p "$DEPLOY_STATE_DIR"
  tmp_file="$(mktemp "$DEPLOY_STATE_DIR/deployed_sha.XXXXXX")"
  printf '%s\n' "$deployed_sha" > "$tmp_file"
  chmod 0644 "$tmp_file"
  mv -f "$tmp_file" "$DEPLOYED_SHA_FILE"
  echo "=== successful deployed SHA recorded: $deployed_sha ==="
}

require_telegram_polling_contract() {
  telegram_transport="$(printf '%s' "${TELEGRAM_TRANSPORT:-polling}" | tr '[:upper:]' '[:lower:]')"
  run_mode="$(printf '%s' "${RUN_MODE:-}" | tr '[:upper:]' '[:lower:]')"

  if [ "$telegram_transport" != "polling" ]; then
    echo "ERROR: Telegram production transport must stay polling; TELEGRAM_TRANSPORT=$telegram_transport"
    exit 20
  fi

  if [ -n "$run_mode" ] && [ "$run_mode" != "polling" ]; then
    echo "ERROR: Telegram production transport must stay polling; RUN_MODE=$run_mode"
    exit 21
  fi

  if _is_truthy "${TELEGRAM_WEBHOOK_ENABLED:-0}"; then
    echo "ERROR: Telegram webhook must stay disabled in production polling mode"
    exit 22
  fi

  if _is_truthy "${TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED:-0}"; then
    echo "ERROR: Telegram legacy token webhook must stay disabled in production polling mode"
    exit 23
  fi

  echo "=== telegram transport contract OK: polling ==="
}

sync_deploy_webhook_service() {
  if [ ! -f "$DEPLOY_WEBHOOK_SOURCE" ]; then
    echo "WARNING: deploy webhook source not found: $DEPLOY_WEBHOOK_SOURCE"
    return 0
  fi

  echo "=== sync deploy webhook service script ==="
  install -m 0644 "$DEPLOY_WEBHOOK_SOURCE" "$DEPLOY_WEBHOOK_TARGET"

  if systemctl list-unit-files "$DEPLOY_WEBHOOK_SERVICE" >/dev/null 2>&1; then
    echo "=== restart deploy webhook service ==="
    systemctl restart "$DEPLOY_WEBHOOK_SERVICE" || true
  else
    echo "WARNING: deploy webhook service not installed: $DEPLOY_WEBHOOK_SERVICE"
  fi
}

require_single_local_main_branch() {
  local branch
  local branch_count
  local branch_list

  echo "=== enforce production git topology: one local main branch ==="
  while IFS= read -r branch; do
    [ -n "$branch" ] || continue
    if [ "$branch" != "main" ]; then
      echo "=== delete stale local production branch: $branch ==="
      git branch -D "$branch"
    fi
  done < <(git for-each-ref --format='%(refname:short)' refs/heads)

  branch_list="$(git for-each-ref --format='%(refname:short)' refs/heads)"
  branch_count="$(printf '%s\n' "$branch_list" | sed '/^$/d' | wc -l | tr -d ' ')"
  if [ "$branch_count" != "1" ] || [ "$branch_list" != "main" ]; then
    echo "ERROR: production server must have exactly one local branch named main"
    echo "ERROR: local branch count=$branch_count branches=$branch_list"
    exit 11
  fi
  echo "=== production branch topology OK: count=1 branch=main ==="
}

publish_server_branch_audit_if_requested() {
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

  remote_branch_list="$(git ls-remote --heads origin | awk '{ref=$2; sub("^refs/heads/", "", ref); print ref}' | sort)"
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
  git push origin main
  echo "=== $audit_message ==="
}

require_telegram_polling_contract

if skip_if_trigger_already_deployed; then
  :
else
  coalesce_code="$?"
  if [ "$coalesce_code" -eq 10 ]; then
    exit 0
  fi
  exit "$coalesce_code"
fi

OLD_SHA="$(git rev-parse HEAD)"
echo "=== old sha: $OLD_SHA ==="

rollback() {
  code="$?"
  echo "=== deploy failed with code=$code at $(date -Is) ==="
  echo "=== rollback to $OLD_SHA ==="
  git reset --hard "$OLD_SHA" || true
  systemctl restart "$SERVICE_NAME" || true
  wait_for_health "$LOCAL_HEALTH_URL" "$HEALTH_WAIT_SECONDS" || true
  systemctl status "$SERVICE_NAME" --no-pager -l || true
  exit "$code"
}

wait_for_health() {
  url="$1"
  timeout_seconds="$2"
  start_ts="$(date +%s)"

  echo "=== wait for health: $url timeout=${timeout_seconds}s ==="
  while true; do
    if curl -fsS --max-time 5 "$url" >/dev/null; then
      echo "=== health OK: $url ==="
      return 0
    fi

    now_ts="$(date +%s)"
    elapsed="$((now_ts - start_ts))"
    if [ "$elapsed" -ge "$timeout_seconds" ]; then
      echo "ERROR: health timeout after ${elapsed}s: $url"
      return 1
    fi
    sleep 2
  done
}

trap rollback ERR

echo "=== git status before ==="
git status --short

if [ -n "$(git status --short)" ]; then
  echo "ERROR: dirty working tree; refusing deploy"
  exit 10
fi

echo "=== checkout production branch main ==="
git checkout main

echo "=== fetch and prune origin ==="
git fetch --prune origin

echo "=== fast-forward only ==="
git merge --ff-only origin/main

require_single_local_main_branch

git remote prune origin
publish_server_branch_audit_if_requested

NEW_SHA="$(git rev-parse HEAD)"
echo "=== new sha: $NEW_SHA ==="

require_telegram_polling_contract
sync_deploy_webhook_service

if [ -f requirements.txt ]; then
  echo "=== install requirements ==="
  "$PIP" install -r requirements.txt
fi

echo "=== compile smoke ==="
"$PYTHON" -m compileall \
  main.py \
  app.py \
  runtime \
  services \
  handlers \
  keyboards \
  scripts

echo "=== prod validator ==="
VALIDATOR_RELEASE_MODE=1 VALIDATOR_GUARDRAILS_STRICT=1 "$PYTHON" scripts/validate_project.py

if [ -f scripts/check_ruff.py ]; then
  echo "=== ruff/project quality check ==="
  "$PYTHON" scripts/check_ruff.py
fi

echo "=== restart service ==="
systemctl restart "$SERVICE_NAME"

echo "=== wait service health ==="
wait_for_health "$LOCAL_HEALTH_URL" "$HEALTH_WAIT_SECONDS"

echo "=== service status ==="
systemctl is-active --quiet "$SERVICE_NAME"
systemctl status "$SERVICE_NAME" --no-pager -l | sed -n '1,60p'

echo "=== public health ==="
wait_for_health "$PUBLIC_HEALTH_URL" "$HEALTH_WAIT_SECONDS"

if [ -f scripts/post_deploy_verify.py ]; then
  echo "=== post deploy verify ==="
  "$PYTHON" scripts/post_deploy_verify.py --skip-pytest
fi

record_successful_deployed_sha "$NEW_SHA"
trap - ERR
echo "=== deploy finished OK: $(date -Is) ==="
echo "=== deployed sha: $NEW_SHA ==="
