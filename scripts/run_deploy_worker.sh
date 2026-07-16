#!/usr/bin/env bash
set -Eeuo pipefail

# Runs in an independent transient systemd service, outside the webhook cgroup.
APP_DIR="${APP_DIR:-/root/metrotherapy}"
DEPLOY_SH="${DEPLOY_SH:-$APP_DIR/deploy.sh}"
PYTHON="${PYTHON:-$APP_DIR/.venv/bin/python}"
LOCK_FILE="${LOCK_FILE:-$APP_DIR/data/deploy/metrotherapy_deploy.lock}"
FLOCK_BIN="${FLOCK_BIN:-/usr/bin/flock}"
LOG_FILE="${LOG_FILE:-/var/log/metrotherapy_deploy.log}"
ENV_FILE="${ENV_FILE:-/etc/metrotherapy/metrotherapy.env}"
MIGRATION_DIR="${MIGRATION_DIR:-/var/lib/metrotherapy/deploy-migrations}"
YOOKASSA_MIGRATION_MARKER="$MIGRATION_DIR/telegram-yookassa-dual-payment-v1.applied"
STARS_PRICE_MIGRATION_MARKER="$MIGRATION_DIR/telegram-stars-explicit-ladder-v1.applied"
STARS_ONLY_MIGRATION_MARKER="$MIGRATION_DIR/telegram-stars-only-checkout-v1.applied"
MAX_API2_MIGRATION_MARKER="$MIGRATION_DIR/max-platform-api2-v1.applied"
MAX_TRUST_MIGRATION_MARKER="$MIGRATION_DIR/max-mincifry-trust-v1.applied"

mkdir -p "$(dirname "$LOCK_FILE")"

if [ ! -x "$FLOCK_BIN" ]; then
  printf 'ERROR: flock is unavailable: %s\n' "$FLOCK_BIN" >> "$LOG_FILE"
  exit 31
fi

# The file is only a stable inode for the kernel lock. It may persist forever.
# The actual lock belongs to FD 9 and is released automatically if this worker
# exits, is killed, or crashes. This prevents a stale sentinel file from
# permanently blocking every future production deploy.
exec 9>"$LOCK_FILE"
if ! "$FLOCK_BIN" -n 9; then
  printf '=== deploy skipped: another worker holds flock %s ===\n' "$(date -Is)" >> "$LOG_FILE"
  exit 0
fi
printf '%s\n' "$$" 1>&9

MIGRATION_PENDING=0
YOOKASSA_MIGRATION_PENDING=0
STARS_PRICE_MIGRATION_PENDING=0
STARS_ONLY_MIGRATION_PENDING=0
MAX_API2_MIGRATION_PENDING=0
MAX_TRUST_MIGRATION_PENDING=0
ENV_BACKUP=""

ensure_env_backup() {
  if [ ! -f "$ENV_FILE" ]; then
    printf 'ERROR: production env file not found for migration: %s\n' "$ENV_FILE" >> "$LOG_FILE"
    exit 30
  fi
  if [ -z "$ENV_BACKUP" ]; then
    ENV_BACKUP="$(mktemp "${ENV_FILE}.deploy-migrations.XXXXXX")"
    cp -a "$ENV_FILE" "$ENV_BACKUP"
  fi
  MIGRATION_PENDING=1
}

cleanup() {
  code="$?"
  if [ "$code" -ne 0 ] && [ "$MIGRATION_PENDING" = "1" ] && [ -n "$ENV_BACKUP" ] && [ -f "$ENV_BACKUP" ]; then
    cp -a "$ENV_BACKUP" "$ENV_FILE" || true
    printf '=== production env migrations rolled back after failed deploy: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
  fi
  rm -f "$ENV_BACKUP" 2>/dev/null || true
  "$FLOCK_BIN" -u 9 || true
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

publish_max_provider_audit_if_requested() {
  local request_message
  local audit_output
  local audit_code
  local audit_message

  request_message="$(git -C "$APP_DIR" log -1 --pretty=%B)"
  case "$request_message" in
    *"[max-provider-audit-request]"*) ;;
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
    if audit_output="$("$PYTHON" "$APP_DIR/scripts/max_provider_audit.py" 2>&1)"; then
      audit_code=0
    else
      audit_code="$?"
    fi
  fi

  audit_output="$(printf '%s' "$audit_output" | tr '\r\n' ' ' | sed 's/[[:space:]][[:space:]]*/ /g' | cut -c1-220)"
  if [ -z "$audit_output" ]; then
    audit_output="status=error stage=runner bot=unknown code=$audit_code error=EMPTY_AUDIT_RESULT"
  fi
  audit_message="[max-provider-audit-result] $audit_output"

  git -C "$APP_DIR" -c user.name="Metrotherapy Deploy Audit" \
      -c user.email="deploy-audit@metrotherapy.local" \
      commit --allow-empty -m "$audit_message"
  git -C "$APP_DIR" push origin main
  printf '=== %s ===\n' "$audit_message" >> "$LOG_FILE"
}

mkdir -p "$MIGRATION_DIR"
if [ ! -e "$YOOKASSA_MIGRATION_MARKER" ]; then
  ensure_env_backup
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
  YOOKASSA_MIGRATION_PENDING=1
  printf '=== applied historical Telegram YooKassa dual-payment migration: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi

if [ ! -e "$STARS_PRICE_MIGRATION_MARKER" ]; then
  ensure_env_backup
  ENV_TMP="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk '
    BEGIN {
      values["TELEGRAM_STARS_PRICING_MODE"] = "explicit"
      values["TELEGRAM_STARS_PRICE_PRACTICE_START_7"] = "1500"
      values["TELEGRAM_STARS_PRICE_PRACTICE_60"] = "2500"
      values["TELEGRAM_STARS_PRICE_PRACTICE_ANTISTRESS_60"] = "5000"
      values["TELEGRAM_STARS_PRICE_PRACTICE_PERSONAL_MONTH"] = "15000"
    }
    {
      split($0, parts, "=")
      key = parts[1]
      if (key in values) {
        if (!written[key]) {
          print key "=" values[key]
          written[key] = 1
        }
        next
      }
      print
    }
    END {
      order[1] = "TELEGRAM_STARS_PRICING_MODE"
      order[2] = "TELEGRAM_STARS_PRICE_PRACTICE_START_7"
      order[3] = "TELEGRAM_STARS_PRICE_PRACTICE_60"
      order[4] = "TELEGRAM_STARS_PRICE_PRACTICE_ANTISTRESS_60"
      order[5] = "TELEGRAM_STARS_PRICE_PRACTICE_PERSONAL_MONTH"
      for (i = 1; i <= 5; i++) {
        key = order[i]
        if (!written[key]) {
          print key "=" values[key]
        }
      }
    }
  ' "$ENV_FILE" > "$ENV_TMP"
  cat "$ENV_TMP" > "$ENV_FILE"
  rm -f "$ENV_TMP"
  STARS_PRICE_MIGRATION_PENDING=1
  printf '=== configured explicit Telegram Stars price ladder: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi

if [ ! -e "$STARS_ONLY_MIGRATION_MARKER" ]; then
  ensure_env_backup
  ENV_TMP="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk '
    BEGIN { written = 0 }
    /^TELEGRAM_YOOKASSA_ENABLED=/ {
      if (!written) {
        print "TELEGRAM_YOOKASSA_ENABLED=0"
        written = 1
      }
      next
    }
    { print }
    END {
      if (!written) {
        print "TELEGRAM_YOOKASSA_ENABLED=0"
      }
    }
  ' "$ENV_FILE" > "$ENV_TMP"
  cat "$ENV_TMP" > "$ENV_FILE"
  rm -f "$ENV_TMP"
  STARS_ONLY_MIGRATION_PENDING=1
  printf '=== disabled Telegram YooKassa; digital packages are Stars-only: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi

if [ ! -e "$MAX_API2_MIGRATION_MARKER" ]; then
  ensure_env_backup
  ENV_TMP="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk '
    BEGIN { written = 0 }
    /^MAX_API_BASE_URL=/ {
      if (!written) {
        print "MAX_API_BASE_URL=https://platform-api2.max.ru"
        written = 1
      }
      next
    }
    { print }
    END {
      if (!written) {
        print "MAX_API_BASE_URL=https://platform-api2.max.ru"
      }
    }
  ' "$ENV_FILE" > "$ENV_TMP"
  cat "$ENV_TMP" > "$ENV_FILE"
  rm -f "$ENV_TMP"
  MAX_API2_MIGRATION_PENDING=1
  printf '=== migrated MAX API base to platform-api2.max.ru: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi

if [ ! -e "$MAX_TRUST_MIGRATION_MARKER" ]; then
  if [ ! -f "$ENV_FILE" ]; then
    printf 'ERROR: production env file not found for MAX trust migration: %s\n' "$ENV_FILE" >> "$LOG_FILE"
    exit 45
  fi
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  if [ -n "${MAX_BOT_TOKEN:-}" ]; then
    PYTHON_BIN="$PYTHON" /usr/bin/bash "$APP_DIR/scripts/install_max_trust.sh" >> "$LOG_FILE" 2>&1
    MAX_TRUST_MIGRATION_PENDING=1
    printf '=== installed verified MAX Minцифры trust chain: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
  else
    printf '=== MAX trust migration deferred: MAX_BOT_TOKEN is empty: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
  fi
fi

printf '=== deploy queued started: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
/usr/bin/bash "$DEPLOY_SH" >> "$LOG_FILE" 2>&1
printf '=== deploy queued finished: %s ===\n' "$(date -Is)" >> "$LOG_FILE"

if [ "$YOOKASSA_MIGRATION_PENDING" = "1" ]; then
  touch "$YOOKASSA_MIGRATION_MARKER"
  printf '=== historical Telegram YooKassa migration committed: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi
if [ "$STARS_PRICE_MIGRATION_PENDING" = "1" ]; then
  touch "$STARS_PRICE_MIGRATION_MARKER"
  printf '=== Telegram Stars price migration committed: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi
if [ "$STARS_ONLY_MIGRATION_PENDING" = "1" ]; then
  touch "$STARS_ONLY_MIGRATION_MARKER"
  printf '=== Telegram Stars-only migration committed: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi
if [ "$MAX_API2_MIGRATION_PENDING" = "1" ]; then
  touch "$MAX_API2_MIGRATION_MARKER"
  printf '=== MAX API2 migration committed: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi
if [ "$MAX_TRUST_MIGRATION_PENDING" = "1" ]; then
  touch "$MAX_TRUST_MIGRATION_MARKER"
  printf '=== MAX Minцифры trust migration committed: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi
if [ "$MIGRATION_PENDING" = "1" ]; then
  rm -f "$ENV_BACKUP"
  ENV_BACKUP=""
  MIGRATION_PENDING=0
fi

publish_stars_provider_audit_if_requested
publish_max_provider_audit_if_requested
