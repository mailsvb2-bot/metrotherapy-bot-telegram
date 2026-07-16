#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/root/metrotherapy}"
LOCK_FILE="${LOCK_FILE:-$APP_DIR/data/deploy/metrotherapy_deploy.lock}"
WORKER_PATH="${WORKER_PATH:-$APP_DIR/scripts/run_deploy_worker.sh}"
FLOCK_BIN="${FLOCK_BIN:-/usr/bin/flock}"
LSLOCKS_BIN="${LSLOCKS_BIN:-/usr/bin/lslocks}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-/usr/bin/systemctl}"
PS_BIN="${PS_BIN:-/usr/bin/ps}"
TIMEOUT_BIN="${TIMEOUT_BIN:-/usr/bin/timeout}"
STALE_AFTER_SECONDS="${STALE_AFTER_SECONDS:-3600}"
LOCK_RELEASE_WAIT_SECONDS="${LOCK_RELEASE_WAIT_SECONDS:-45}"
SYSTEMCTL_STOP_TIMEOUT_SECONDS="${SYSTEMCTL_STOP_TIMEOUT_SECONDS:-60}"
ALLOW_LEGACY_LOCK_METADATA="${ALLOW_LEGACY_LOCK_METADATA:-0}"
ALLOW_YOUNG_STALE_WORKER="${ALLOW_YOUNG_STALE_WORKER:-0}"
UNIT_PATTERN='metrotherapy-deploy-*.service'

fail() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

is_non_negative_integer() {
  local value="${1:-}"
  case "$value" in
    ''|*[!0-9]*) return 1 ;;
  esac
  return 0
}

is_positive_integer() {
  local value="${1:-}"
  is_non_negative_integer "$value" && [ "$value" -gt 0 ]
}

if [ "$(id -u)" -ne 0 ]; then
  fail "run this recovery as root"
fi
for binary in \
  "$FLOCK_BIN" \
  "$LSLOCKS_BIN" \
  "$SYSTEMCTL_BIN" \
  "$PS_BIN" \
  "$TIMEOUT_BIN"
do
  [ -x "$binary" ] || fail "required executable is unavailable: $binary"
done
is_non_negative_integer "$STALE_AFTER_SECONDS" \
  || fail "STALE_AFTER_SECONDS must be a non-negative integer"
is_non_negative_integer "$LOCK_RELEASE_WAIT_SECONDS" \
  || fail "LOCK_RELEASE_WAIT_SECONDS must be a non-negative integer"
is_positive_integer "$SYSTEMCTL_STOP_TIMEOUT_SECONDS" \
  || fail "SYSTEMCTL_STOP_TIMEOUT_SECONDS must be a positive integer"
[ -f "$LOCK_FILE" ] || fail "deploy lock file does not exist: $LOCK_FILE"

# Opening read/write without truncation preserves the active holder metadata.
exec 8<>"$LOCK_FILE"
if "$FLOCK_BIN" -n 8; then
  "$FLOCK_BIN" -u 8 || true
  fail "lock file is not currently held; refusing to stop any unit"
fi

holder_pids="$(
  "$LSLOCKS_BIN" --noheadings --raw --output PID,PATH 2>/dev/null \
    | awk -v path="$LOCK_FILE" '$2 == path && $1 ~ /^[0-9]+$/ {print $1}' \
    | sort -u
)"
holder_count="$(printf '%s\n' "$holder_pids" | sed '/^$/d' | wc -l | tr -d ' ')"
[ "$holder_count" -eq 1 ] \
  || fail "expected exactly one kernel lock holder for $LOCK_FILE; found $holder_count"
holder_pid="$(printf '%s\n' "$holder_pids" | sed -n '1p')"
[ "$holder_pid" -gt 1 ] || fail "refusing invalid deploy lock holder PID: $holder_pid"
kill -0 "$holder_pid" 2>/dev/null \
  || fail "kernel lock holder PID is not running: $holder_pid"

cmdline="$(tr '\0' ' ' < "/proc/$holder_pid/cmdline" 2>/dev/null || true)"
printf '%s' "$cmdline" | grep -F -- "$WORKER_PATH" >/dev/null \
  || fail "kernel lock holder is not the canonical deploy worker"

metadata_version=""
metadata_pid=""
lock_acquired_epoch=""
metadata_trigger=""
IFS=' ' read -r \
  metadata_version \
  metadata_pid \
  lock_acquired_epoch \
  metadata_trigger \
  < "$LOCK_FILE" || true

now_epoch="$(date +%s)"
is_non_negative_integer "$now_epoch" || fail "could not determine current epoch"
metadata_mode="v1"
if [ "$metadata_version" = "v1" ]; then
  case "$metadata_pid" in
    ''|*[!0-9]*) fail "v1 lock metadata contains an invalid holder PID" ;;
  esac
  [ "$metadata_pid" = "$holder_pid" ] \
    || fail "v1 lock metadata PID does not match the kernel lock holder"
  is_non_negative_integer "$lock_acquired_epoch" \
    || fail "v1 lock metadata contains an invalid acquisition epoch"
  [ "$lock_acquired_epoch" -le "$now_epoch" ] \
    || fail "v1 lock acquisition epoch is in the future"
  held_seconds="$((now_epoch - lock_acquired_epoch))"
else
  metadata_mode="legacy-explicit-override"
  [ "$ALLOW_LEGACY_LOCK_METADATA" = "1" ] \
    || fail "legacy or missing lock metadata; set ALLOW_LEGACY_LOCK_METADATA=1 only after operator inspection"
  held_seconds="$("$PS_BIN" -o etimes= -p "$holder_pid" | tr -d '[:space:]')"
  is_non_negative_integer "$held_seconds" \
    || fail "could not determine legacy deploy worker elapsed time"
fi

matching_unit=""
matching_count=0
while IFS= read -r unit; do
  [ -n "$unit" ] || continue
  if [[ ! "$unit" =~ ^metrotherapy-deploy-[0-9a-f]{12}\.service$ ]]; then
    continue
  fi
  main_pid="$("$SYSTEMCTL_BIN" show "$unit" -p MainPID --value 2>/dev/null || true)"
  if [ "$main_pid" = "$holder_pid" ]; then
    matching_unit="$unit"
    matching_count="$((matching_count + 1))"
  fi
done < <(
  "$SYSTEMCTL_BIN" list-units \
    --all \
    --type=service \
    --no-legend \
    --plain \
    "$UNIT_PATTERN" \
    | awk '{print $1}'
)

[ "$matching_count" -eq 1 ] \
  || fail "expected exactly one transient deploy unit for kernel lock holder; found $matching_count"

printf 'STALE_DEPLOY_RECOVERY_TARGET=%s\n' "$matching_unit"
printf 'STALE_DEPLOY_RECOVERY_PID=%s\n' "$holder_pid"
printf 'STALE_DEPLOY_RECOVERY_METADATA_MODE=%s\n' "$metadata_mode"
printf 'STALE_DEPLOY_RECOVERY_HELD_SECONDS=%s\n' "$held_seconds"
printf 'STALE_DEPLOY_RECOVERY_THRESHOLD_SECONDS=%s\n' "$STALE_AFTER_SECONDS"

if [ "$held_seconds" -lt "$STALE_AFTER_SECONDS" ] \
  && [ "$ALLOW_YOUNG_STALE_WORKER" != "1" ]; then
  fail "deploy lock has been held for only ${held_seconds}s; stale threshold is ${STALE_AFTER_SECONDS}s"
fi

if ! "$TIMEOUT_BIN" \
  --signal=TERM \
  --kill-after=15s \
  "$SYSTEMCTL_STOP_TIMEOUT_SECONDS" \
  "$SYSTEMCTL_BIN" stop "$matching_unit"
then
  fail "timed out or failed while stopping the exact stale deploy unit"
fi

if ! "$FLOCK_BIN" -w "$LOCK_RELEASE_WAIT_SECONDS" 8; then
  fail "stopped unit but deploy lock was not released within ${LOCK_RELEASE_WAIT_SECONDS}s"
fi
"$FLOCK_BIN" -u 8 || true

if kill -0 "$holder_pid" 2>/dev/null; then
  fail "stopped unit but holder PID is still running"
fi

printf 'STALE_DEPLOY_RECOVERY_OK\n'
printf 'NEXT_ACTION=trigger one signed production recovery request for current main\n'
