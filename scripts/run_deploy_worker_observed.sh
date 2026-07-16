#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/root/metrotherapy}"
INNER_WORKER="${DEPLOY_INNER_WORKER:-$APP_DIR/scripts/run_deploy_worker.sh}"
LOG_FILE="${LOG_FILE:-/var/log/metrotherapy_deploy.log}"
TRIGGER_SHA="${DEPLOY_TRIGGER_SHA:-}"

/usr/bin/bash "$INNER_WORKER"
printf '=== deploy worker completed trigger=%s: %s ===\n' "$TRIGGER_SHA" "$(date -Is)" >> "$LOG_FILE"
