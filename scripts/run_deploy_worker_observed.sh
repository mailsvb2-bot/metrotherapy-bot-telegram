#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/root/metrotherapy}"
INNER_WORKER="${DEPLOY_INNER_WORKER:-$APP_DIR/scripts/run_deploy_worker.sh}"
LIVE_PROOF_RUNNER="${LIVE_PROOF_RUNNER:-$APP_DIR/scripts/production_live_proofs.py}"
LOG_FILE="${LOG_FILE:-/var/log/metrotherapy_deploy.log}"
TRIGGER_SHA="${DEPLOY_TRIGGER_SHA:-}"
TRIGGER_MESSAGE=""

# Audit-result commits are immutable evidence, not deploy requests. Read the
# trigger-bound message before invoking the inner worker so a result commit can
# never recursively start another production rollout.
if [ -n "$TRIGGER_SHA" ]; then
  git -C "$APP_DIR" fetch origin main >/dev/null 2>&1 || true
  TRIGGER_MESSAGE="$(git -C "$APP_DIR" show -s --format=%B "$TRIGGER_SHA" 2>/dev/null || true)"
fi
case "$TRIGGER_MESSAGE" in
  *"[ops-live-proof-result]"*|*"_ops-live-proof-result_"*|*"[deploy-failure-result]"*)
    printf '=== observed audit result trigger skipped=%s: %s ===\n' "$TRIGGER_SHA" "$(date -Is)" >> "$LOG_FILE"
    exit 0
    ;;
esac

publish_deploy_failure_result() {
  local inner_code="$1"
  local stage="unknown"
  local bounded_code="$inner_code"
  local segment=""
  local matched=""
  local parent_sha=""
  local tree_sha=""
  local result_sha=""
  local message=""
  local attempt=""

  # Inspect only the log segment belonging to this immutable trigger. Raw log
  # text never leaves the server; only an allowlisted category and numeric code
  # are committed as evidence.
  if [ -f "$LOG_FILE" ] && [ -n "$TRIGGER_SHA" ]; then
    segment="$(awk -v needle="=== deploy trigger sha: $TRIGGER_SHA ===" '
      $0 == needle { found=1; buf="" }
      found { print }
    ' "$LOG_FILE" | tail -n 800)"
  fi

  if printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=mandatory production backup, restore and readiness gate code='; then
    stage="production_gate_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=mandatory production backup, restore and readiness gate code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=candidate strict validator and expand migrations code='; then
    stage="candidate_validator_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=candidate strict validator and expand migrations code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=previous release compatibility on expanded schema code='; then
    stage="schema_compat_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=previous release compatibility on expanded schema code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=build immutable release '; then
    stage="release_build_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=build immutable release ' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=restart metrotherapy service code='; then
    stage="runtime_restart_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=restart metrotherapy service code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=migrate authoritative privacy export environment code='; then
    stage="env_migration_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=migrate authoritative privacy export environment code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=validate production runtime contract after env migration code='; then
    stage="runtime_contract_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=validate production runtime contract after env migration code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED health timeout:'; then
    stage="health_failed"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED dirty source worktree'; then
    stage="dirty_source_failed"
  fi

  if [ -n "$matched" ]; then
    case "$matched" in
      *' code='*)
        bounded_code="${matched##* code=}"
        bounded_code="${bounded_code%%[^0-9]*}"
        ;;
    esac
  fi
  case "$bounded_code" in
    ''|*[!0-9]*) bounded_code="$inner_code" ;;
  esac
  case "$inner_code" in
    ''|*[!0-9]*) inner_code="1" ;;
  esac

  message="[deploy-failure-result] trigger=${TRIGGER_SHA:0:12} status=error stage=$stage code=$bounded_code worker_code=$inner_code"

  for attempt in 1 2 3; do
    git -C "$APP_DIR" fetch origin main >/dev/null 2>&1 || true
    parent_sha="$(git -C "$APP_DIR" rev-parse origin/main 2>/dev/null || true)"
    tree_sha="$(git -C "$APP_DIR" rev-parse "$parent_sha^{tree}" 2>/dev/null || true)"
    if [ -z "$parent_sha" ] || [ -z "$tree_sha" ]; then
      sleep "$attempt"
      continue
    fi
    result_sha="$(
      printf '%s\n' "$message" |
        git -C "$APP_DIR" \
          -c user.name="Metrotherapy Deploy Failure Audit" \
          -c user.email="deploy-failure-audit@metrotherapy.local" \
          commit-tree "$tree_sha" -p "$parent_sha" -F - 2>/dev/null || true
    )"
    if [ -n "$result_sha" ] && git -C "$APP_DIR" push origin "$result_sha:refs/heads/main" >/dev/null 2>&1; then
      printf '=== %s ===\n' "$message" >> "$LOG_FILE"
      return 0
    fi
    sleep "$attempt"
  done

  printf 'ERROR: unable to publish secret-safe deploy failure evidence trigger=%s stage=%s code=%s\n' \
    "$TRIGGER_SHA" "$stage" "$bounded_code" >> "$LOG_FILE"
  return 0
}

set +e
/usr/bin/bash "$INNER_WORKER"
INNER_CODE="$?"
set -e
if [ "$INNER_CODE" -ne 0 ]; then
  publish_deploy_failure_result "$INNER_CODE"
  exit "$INNER_CODE"
fi

case "$TRIGGER_MESSAGE" in
  *"[vk-confirmation-sync-request]"*|*"[rollback-live-proof-request]"*)
    if [ ! -f "$LIVE_PROOF_RUNNER" ]; then
      printf 'ERROR: production live proof runner is missing: %s\n' "$LIVE_PROOF_RUNNER" >> "$LOG_FILE"
      exit 41
    fi
    /usr/bin/python3 "$LIVE_PROOF_RUNNER" >> "$LOG_FILE" 2>&1
    ;;
esac

printf '=== deploy worker completed trigger=%s: %s ===\n' "$TRIGGER_SHA" "$(date -Is)" >> "$LOG_FILE"
