#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/root/metrotherapy}"
DEPLOY_SH="${DEPLOY_SH:-$APP_DIR/deploy.sh}"
LOCK_FILE="${LOCK_FILE:-$APP_DIR/data/deploy/metrotherapy_deploy.lock}"
LOG_FILE="${LOG_FILE:-/var/log/metrotherapy_deploy.log}"

mkdir -p "$(dirname "$LOCK_FILE")"

if [ -e "$LOCK_FILE" ]; then
  printf '=== deploy skipped: lock exists %s ===\n' "$(date -Is)" >> "$LOG_FILE"
  exit 0
fi

touch "$LOCK_FILE"
cleanup() {
  rm -f "$LOCK_FILE"
}
trap cleanup EXIT INT TERM HUP

printf '=== deploy queued started: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
/usr/bin/bash "$DEPLOY_SH" >> "$LOG_FILE" 2>&1
printf '=== deploy queued finished: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
