#!/usr/bin/env bash
set -Eeuo pipefail

# Runs in an independent transient systemd service, outside the webhook cgroup.
APP_DIR="${APP_DIR:-/root/metrotherapy}"
DEPLOY_SH="${DEPLOY_SH:-$APP_DIR/deploy.sh}"
PYTHON="${PYTHON:-$APP_DIR/.venv/bin/python}"
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

publish_stars_provider_audit_if_requested() {
  local request_message
  local audit_output
  local audit_code
  local audit_message

  request_message="$(git -C "$APP_DIR" log -1 --pretty=%B)"
  case "$request_message" in
    *"[stars-provider-audit-request]"*) ;;
    *) return 0 ;;
  esac

  if [ ! -f "$ENV_FILE" ]; then
    audit_output="status=error stage=config bot=unknown code=0 error=ENV_FILE_MISSING"
    audit_code=2
  else
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    if audit_output="$("$PYTHON" "$APP_DIR/scripts/telegram_stars_provider_audit.py" 2>&1)"; then
      audit_code=0
    else
      audit_code="$?"
    fi
  fi

  audit_output="$(printf '%s' "$audit_output" | tr '\r\n' ' ' | sed 's/[[:space:]][[:space:]]*/ /g' | cut -c1-180)"
  if [ -z "$audit_output" ]; then
    audit_output="status=error stage=runner bot=unknown code=$audit_code error=EMPTY_AUDIT_RESULT"
  fi
  audit_message="[stars-provider-audit-result] $audit_output"

  git -C "$APP_DIR" -c user.name="Metrotherapy Deploy Audit" \
      -c user.email="deploy-audit@metrotherapy.local" \
      commit --allow-empty -m "$audit_message"
  git -C "$APP_DIR" push origin main
  printf '=== %s ===\n' "$audit_message" >> "$LOG_FILE"
}

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

publish_stars_provider_audit_if_requested
