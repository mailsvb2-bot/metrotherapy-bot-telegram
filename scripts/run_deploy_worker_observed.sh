#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/root/metrotherapy}"
INNER_WORKER="${DEPLOY_INNER_WORKER:-$APP_DIR/scripts/run_deploy_worker.sh}"
LIVE_PROOF_RUNNER="${LIVE_PROOF_RUNNER:-$APP_DIR/scripts/production_live_proofs.py}"
LOG_FILE="${LOG_FILE:-/var/log/metrotherapy_deploy.log}"
TRIGGER_SHA="${DEPLOY_TRIGGER_SHA:-}"
TRIGGER_MESSAGE=""

# Live-proof result commits are immutable audit evidence, not deploy requests.
# Read the trigger-bound message before invoking the inner worker so publishing
# a result cannot cause a second production rollout.
if [ -n "$TRIGGER_SHA" ]; then
  git -C "$APP_DIR" fetch origin main >/dev/null 2>&1 || true
  TRIGGER_MESSAGE="$(git -C "$APP_DIR" show -s --format=%B "$TRIGGER_SHA" 2>/dev/null || true)"
fi
case "$TRIGGER_MESSAGE" in
  *"[ops-live-proof-result]"*)
    printf '=== live-proof result trigger skipped=%s: %s ===\n' "$TRIGGER_SHA" "$(date -Is)" >> "$LOG_FILE"
    exit 0
    ;;
esac

/usr/bin/bash "$INNER_WORKER"

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
