#!/usr/bin/env bash
set -Eeuo pipefail

# Runs in an independent transient systemd service, outside the webhook cgroup.
APP_DIR="${APP_DIR:-/root/metrotherapy}"
DEPLOY_SH="${DEPLOY_SH:-$APP_DIR/deploy.sh}"
PYTHON="${PYTHON:-$APP_DIR/.venv/bin/python}"
LOCK_FILE="${LOCK_FILE:-$APP_DIR/data/deploy/metrotherapy_deploy.lock}"
FLOCK_BIN="${FLOCK_BIN:-/usr/bin/flock}"
LOCK_WAIT_SECONDS="${DEPLOY_LOCK_WAIT_SECONDS:-900}"
TRIGGER_SHA="${DEPLOY_TRIGGER_SHA:-}"
LOG_FILE="${LOG_FILE:-/var/log/metrotherapy_deploy.log}"
ENV_FILE="${ENV_FILE:-/etc/metrotherapy/metrotherapy.env}"
MIGRATION_DIR="${MIGRATION_DIR:-/var/lib/metrotherapy/deploy-migrations}"
YOOKASSA_MIGRATION_MARKER="$MIGRATION_DIR/telegram-yookassa-dual-payment-v1.applied"
STARS_PRICE_MIGRATION_MARKER="$MIGRATION_DIR/telegram-stars-explicit-ladder-v1.applied"
STARS_ONLY_MIGRATION_MARKER="$MIGRATION_DIR/telegram-stars-only-checkout-v1.applied"
MAX_API2_MIGRATION_MARKER="$MIGRATION_DIR/max-platform-api2-v1.applied"
MAX_TRUST_MIGRATION_MARKER="$MIGRATION_DIR/max-mincifry-trust-v1.applied"
VK_CALLBACK_MIGRATION_MARKER="$MIGRATION_DIR/vk-callback-runtime-v1.applied"

mkdir -p "$(dirname "$LOCK_FILE")"

if [ ! -x "$FLOCK_BIN" ]; then
  printf 'ERROR: flock is unavailable: %s\n' "$FLOCK_BIN" >> "$LOG_FILE"
  exit 31
fi
case "$LOCK_WAIT_SECONDS" in
  ''|*[!0-9]*)
    printf 'ERROR: DEPLOY_LOCK_WAIT_SECONDS must be a non-negative integer: %s\n' "$LOCK_WAIT_SECONDS" >> "$LOG_FILE"
    exit 32
    ;;
esac
if [ -n "$TRIGGER_SHA" ]; then
  case "$TRIGGER_SHA" in
    *[!0-9a-f]*)
      printf 'ERROR: DEPLOY_TRIGGER_SHA must be a lowercase hexadecimal commit: %s\n' "$TRIGGER_SHA" >> "$LOG_FILE"
      exit 35
      ;;
  esac
  if [ "${#TRIGGER_SHA}" -ne 40 ] || [ "$TRIGGER_SHA" = "0000000000000000000000000000000000000000" ]; then
    printf 'ERROR: DEPLOY_TRIGGER_SHA must be one non-zero 40-character commit: %s\n' "$TRIGGER_SHA" >> "$LOG_FILE"
    exit 35
  fi
fi

# The file is only a stable inode for the kernel lock. It may persist forever.
# The actual lock belongs to FD 9 and is released automatically if this worker
# exits, is killed, or crashes. Workers wait in order instead of dropping an
# authenticated push while another deploy is still validating or restarting.
exec 9>"$LOCK_FILE"
printf '=== deploy waiting for flock timeout=%ss: %s ===\n' "$LOCK_WAIT_SECONDS" "$(date -Is)" >> "$LOG_FILE"
if ! "$FLOCK_BIN" -w "$LOCK_WAIT_SECONDS" 9; then
  printf 'ERROR: deploy lock wait timed out after %ss: %s\n' "$LOCK_WAIT_SECONDS" "$(date -Is)" >> "$LOG_FILE"
  exit 33
fi
printf '%s\n' "$$" 1>&9

if [ -n "$TRIGGER_SHA" ]; then
  git -C "$APP_DIR" fetch origin main
  if ! git -C "$APP_DIR" cat-file -e "$TRIGGER_SHA^{commit}" 2>/dev/null; then
    if ! git -C "$APP_DIR" fetch origin "$TRIGGER_SHA"; then
      printf 'ERROR: deploy trigger commit is unavailable: %s\n' "$TRIGGER_SHA" >> "$LOG_FILE"
      exit 36
    fi
  fi
  TRIGGER_MESSAGE="$(git -C "$APP_DIR" show -s --format=%B "$TRIGGER_SHA")"
else
  # Compatibility for an operator invoking the worker manually. Authenticated
  # webhook deliveries always provide DEPLOY_TRIGGER_SHA.
  TRIGGER_SHA="$(git -C "$APP_DIR" rev-parse HEAD)"
  TRIGGER_MESSAGE="$(git -C "$APP_DIR" log -1 --pretty=%B 2>/dev/null || true)"
  printf '=== deploy trigger fallback to local HEAD: %s ===\n' "$TRIGGER_SHA" >> "$LOG_FILE"
fi
printf '=== deploy trigger sha: %s ===\n' "$TRIGGER_SHA" >> "$LOG_FILE"

case "$TRIGGER_MESSAGE" in
  *"[max-trust-install-result]"*|*"[stars-provider-audit-result]"*|*"[max-provider-audit-result]"*|*"[vk-provider-audit-result]"*)
    printf '=== deploy skipped after published provider result trigger=%s: %s ===\n' "$TRIGGER_SHA" "$(date -Is)" >> "$LOG_FILE"
    exit 0
    ;;
esac

MIGRATION_PENDING=0
YOOKASSA_MIGRATION_PENDING=0
STARS_PRICE_MIGRATION_PENDING=0
STARS_ONLY_MIGRATION_PENDING=0
MAX_API2_MIGRATION_PENDING=0
MAX_TRUST_MIGRATION_PENDING=0
VK_CALLBACK_MIGRATION_PENDING=0
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

sanitize_result() {
  printf '%s' "$1" \
    | tr '\r\n' ' ' \
    | sed 's/[[:space:]][[:space:]]*/ /g' \
    | sed 's/[^A-Za-z0-9_ .:=-]/_/g' \
    | cut -c1-220
}

publish_result_commit() {
  local message="$1"
  local attempt
  local parent_sha
  local tree_sha
  local result_sha

  # Build the empty result commit from the newest remote main without checking
  # it out. The production worktree therefore remains pinned to code that was
  # actually deployed by this worker. Every queued worker is bound to its own
  # immutable trigger SHA, so a newer request cannot be mistaken for this result.
  for attempt in 1 2 3; do
    git -C "$APP_DIR" fetch origin main
    if [ "$(git -C "$APP_DIR" log -1 --format=%B origin/main)" = "$message" ]; then
      printf '=== audit result already published at remote tip: %s ===\n' "$message" >> "$LOG_FILE"
      return 0
    fi
    parent_sha="$(git -C "$APP_DIR" rev-parse origin/main)"
    tree_sha="$(git -C "$APP_DIR" rev-parse "$parent_sha^{tree}")"
    result_sha="$(
      printf '%s\n' "$message" |
        git -C "$APP_DIR" \
          -c user.name="Metrotherapy Deploy Audit" \
          -c user.email="deploy-audit@metrotherapy.local" \
          commit-tree "$tree_sha" -p "$parent_sha" -F -
    )"

    if git -C "$APP_DIR" push origin "$result_sha:refs/heads/main"; then
      printf '=== %s ===\n' "$message" >> "$LOG_FILE"
      return 0
    fi
    printf '=== audit result push raced with main; retry=%s ===\n' "$attempt" >> "$LOG_FILE"
    sleep "$attempt"
  done

  printf 'ERROR: unable to publish audit result after retries: %s\n' "$message" >> "$LOG_FILE"
  return 34
}

publish_max_trust_install_error() {
  local code="$1"
  local output="$2"
  local safe_output
  local message

  safe_output="$(sanitize_result "$output")"
  if [ -z "$safe_output" ]; then
    safe_output="EMPTY_INSTALLER_RESULT"
  fi
  message="[max-trust-install-result] trigger=${TRIGGER_SHA:0:12} status=error code=$code error=$safe_output"
  publish_result_commit "$message"
}

publish_stars_provider_audit_if_requested() {
  local request_message
  local audit_output
  local audit_code
  local audit_message

  request_message="$TRIGGER_MESSAGE"
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
  audit_message="[stars-provider-audit-result] trigger=${TRIGGER_SHA:0:12} $audit_output"
  publish_result_commit "$audit_message"
}

publish_max_provider_audit_if_requested() {
  local request_message
  local audit_output
  local audit_code
  local audit_message

  request_message="$TRIGGER_MESSAGE"
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
  audit_message="[max-provider-audit-result] trigger=${TRIGGER_SHA:0:12} $audit_output"
  publish_result_commit "$audit_message"
}

publish_vk_provider_audit_if_requested() {
  local request_message
  local audit_output
  local audit_code
  local audit_message

  request_message="$TRIGGER_MESSAGE"
  case "$request_message" in
    *"[vk-provider-audit-request]"*) ;;
    *) return 0 ;;
  esac

  if [ ! -f "$ENV_FILE" ]; then
    audit_output="status=error stage=config group=unknown code=0 error=ENV_FILE_MISSING"
    audit_code=2
  else
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    if audit_output="$("$PYTHON" "$APP_DIR/scripts/vk_provider_audit.py" 2>&1)"; then
      audit_code=0
    else
      audit_code="$?"
    fi
  fi

  audit_output="$(sanitize_result "$audit_output")"
  if [ -z "$audit_output" ]; then
    audit_output="status=error stage=runner group=unknown code=$audit_code error=EMPTY_AUDIT_RESULT"
  fi
  audit_message="[vk-provider-audit-result] trigger=${TRIGGER_SHA:0:12} $audit_output"
  publish_result_commit "$audit_message"
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
    TRUST_OUTPUT=""
    if TRUST_OUTPUT="$(PYTHON_BIN="$PYTHON" /usr/bin/bash "$APP_DIR/scripts/install_max_trust.sh" 2>&1)"; then
      printf '%s\n' "$TRUST_OUTPUT" >> "$LOG_FILE"
      MAX_TRUST_MIGRATION_PENDING=1
      printf '=== installed verified MAX Minцифры trust chain: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
    else
      TRUST_CODE="$?"
      printf '%s\n' "$TRUST_OUTPUT" >> "$LOG_FILE"
      publish_max_trust_install_error "$TRUST_CODE" "$TRUST_OUTPUT"
      exit "$TRUST_CODE"
    fi
  else
    printf '=== MAX trust migration deferred: MAX_BOT_TOKEN is empty: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
  fi
fi

if [ ! -e "$VK_CALLBACK_MIGRATION_MARKER" ]; then
  ensure_env_backup
  ENV_TMP="$(mktemp "${ENV_FILE}.tmp.XXXXXX")"
  awk '
    BEGIN {
      values["VK_CALLBACK_SNACKBAR_ENABLED"] = "1"
      values["VK_AUDIO_UPLOAD_RETRIES"] = "3"
      values["VK_AUDIO_UPLOAD_RETRY_BACKOFF_SEC"] = "0.5"
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
      order[1] = "VK_CALLBACK_SNACKBAR_ENABLED"
      order[2] = "VK_AUDIO_UPLOAD_RETRIES"
      order[3] = "VK_AUDIO_UPLOAD_RETRY_BACKOFF_SEC"
      for (i = 1; i <= 3; i++) {
        key = order[i]
        if (!written[key]) {
          print key "=" values[key]
        }
      }
    }
  ' "$ENV_FILE" > "$ENV_TMP"
  cat "$ENV_TMP" > "$ENV_FILE"
  rm -f "$ENV_TMP"
  VK_CALLBACK_MIGRATION_PENDING=1
  printf '=== configured VK callback acknowledgements and audio retries: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi

printf '=== deploy queued started trigger=%s: %s ===\n' "$TRIGGER_SHA" "$(date -Is)" >> "$LOG_FILE"
/usr/bin/bash "$DEPLOY_SH" >> "$LOG_FILE" 2>&1
printf '=== deploy queued finished trigger=%s: %s ===\n' "$TRIGGER_SHA" "$(date -Is)" >> "$LOG_FILE"

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
if [ "$VK_CALLBACK_MIGRATION_PENDING" = "1" ]; then
  touch "$VK_CALLBACK_MIGRATION_MARKER"
  printf '=== VK callback runtime migration committed: %s ===\n' "$(date -Is)" >> "$LOG_FILE"
fi
if [ "$MIGRATION_PENDING" = "1" ]; then
  rm -f "$ENV_BACKUP"
  ENV_BACKUP=""
  MIGRATION_PENDING=0
fi

publish_stars_provider_audit_if_requested
publish_max_provider_audit_if_requested
publish_vk_provider_audit_if_requested
