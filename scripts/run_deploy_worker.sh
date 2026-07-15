#!/usr/bin/env bash
set -Eeuo pipefail

# Runs in an independent transient systemd service, outside the webhook cgroup.
APP_DIR="${APP_DIR:-/root/metrotherapy}"
DEPLOY_SH="${DEPLOY_SH:-$APP_DIR/deploy.sh}"
LOCK_FILE="${LOCK_FILE:-$APP_DIR/data/deploy/metrotherapy_deploy.lock}"
LOG_FILE="${LOG_FILE:-/var/log/metrotherapy_deploy.log}"
ENV_FILE="${ENV_FILE:-/etc/metrotherapy/metrotherapy.env}"
MIGRATION_DIR="${MIGRATION_DIR:-/var/lib/metrotherapy/deploy-migrations}"
YOOKASSA_MIGRATION_MARKER="$MIGRATION_DIR/telegram-yookassa-dual-payment-v1.applied"

mkdir -p "$(dirname "$LOCK_FILE")"

if [ -e "$LOCK_FILE" ]; then
  printf '=== deploy skipped: lock exists %s ===\n' "$(date -Is)" >> "$LOG_FILE"
  exit 0
fi

touch "$LOCK_FILE"
MIGRATION_PENDING=0
ENV_BACKUP=""

cleanup() {
  code="$?"
  if [ "$code" -ne 0 ] && [ "$MIGRATION_PENDING" = "1" ] && [ -n "$ENV_BACKUP" ] && [ -f "$ENV_BACKUP" ]; then
    cp -a "$ENV_BACKUP" "$ENV_FILE" || true
    printf '=== Telegram YooKassa env migration rolled back after failed deploy: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
  fi
  rm -f "$ENV_BACKUP" 2>/dev/null || true
  rm -f "$LOCK_FILE"
}
trap cleanup EXIT INT TERM HUP

mkdir -p "$MIGRATION_DIR"
if [ ! -e "$YOOKASSA_MIGRATION_MARKER" ]; then
  if [ ! -f "$ENV_FILE" ]; then
    printf 'ERROR: production env file not found for Telegram YooKassa migration: %s\n' "$ENV_FILE" >> "$LOG_FILE"
    exit 30
  fi

  ENV_BACKUP="$(mktemp "${ENV_FILE}.telegram-yookassa-v1.XXXXXX")"
  cp -a "$ENV_FILE" "$ENV_BACKUP"
  ENV_TMP="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk '
    BEGIN { written = 0 }
    /^TELEGRAM_YOOKASSA_ENABLED=/ {
      if (!written) {
        print "TELEGRAM_YOOKASSA_ENABLED=1"
        written = 1
      }
      next
    }
    { print }
    END {
      if (!written) {
        print "TELEGRAM_YOOKASSA_ENABLED=1"
      }
    }
  ' "$ENV_FILE" > "$ENV_TMP"
  cat "$ENV_TMP" > "$ENV_FILE"
  rm -f "$ENV_TMP"
  MIGRATION_PENDING=1
  printf '=== enabled TELEGRAM_YOOKASSA_ENABLED=1 for dual Telegram payments: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi

printf '=== deploy queued started: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
/usr/bin/bash "$DEPLOY_SH" >> "$LOG_FILE" 2>&1
printf '=== deploy queued finished: %s ===\n' "$(date -Is)" >> "$LOG_FILE"

if [ "$MIGRATION_PENDING" = "1" ]; then
  touch "$YOOKASSA_MIGRATION_MARKER"
  rm -f "$ENV_BACKUP"
  ENV_BACKUP=""
  MIGRATION_PENDING=0
  printf '=== Telegram YooKassa env migration committed: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi
