#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/root/metrotherapy}"
LOCK_FILE="${LOCK_FILE:-$APP_DIR/data/deploy/metrotherapy_deploy.lock}"
WORKER_PATH="${WORKER_PATH:-$APP_DIR/scripts/run_deploy_worker.sh}"
FLOCK_BIN="${FLOCK_BIN:-/usr/bin/flock}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-/usr/bin/systemctl}"
PS_BIN="${PS_BIN:-/usr/bin/ps}"
STALE_AFTER_SECONDS="${STALE_AFTER_SECONDS:-1200}"
LOCK_RELEASE_WAIT_SECONDS="${LOCK_RELEASE_WAIT_SECONDS:-45}"
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

if [ "$(id -u)" -ne 0 ]; then
  fail "run this recovery as root"
fi
for binary in "$FLOCK_BIN" "$SYSTEMCTL_BIN" "$PS_BIN"; do
  [ -x "$binary" ] || fail "required executable is unavailable: $binary"
done
is_non_negative_integer "$STALE_AFTER_SECONDS" \
  || fail "STALE_AFTER_SECONDS must be a non-negative integer"
is_non_negative_integer "$LOCK_RELEASE_WAIT_SECONDS" \
  || fail "LOCK_RELEASE_WAIT_SECONDS must be a non-negative integer"
[ -f "$LOCK_FILE" ] || fail "deploy lock file does not exist: $LOCK_FILE"

holder_pid="$(sed -n '1{s/[^0-9].*$//;p;}' "$LOCK_FILE")"
case "$holder_pid" in
  ''|*[!0-9]*) fail "deploy lock does not contain one numeric holder PID" ;;
esac
[ "$holder_pid" -gt 1 ] || fail "refusing invalid deploy lock holder PID: $holder_pid"
kill -0 "$holder_pid" 2>/dev/null \
  || fail "deploy lock holder PID is not running: $holder_pid"

cmdline="$(tr '\0' ' ' < "/proc/$holder_pid/cmdline" 2>/dev/null || true)"
printf '%s' "$cmdline" | grep -F -- "$WORKER_PATH" >/dev/null \
  || fail "lock holder is not the canonical deploy worker"

elapsed_seconds="$("$PS_BIN" -o etimes= -p "$holder_pid" | tr -d '[:space:]')"
is_non_negative_integer "$elapsed_seconds" \
  || fail "could not determine deploy worker elapsed time"
if [ "$elapsed_seconds" -lt "$STALE_AFTER_SECONDS" ] \
  && [ "$ALLOW_YOUNG_STALE_WORKER" != "1" ]; then
  fail "deploy worker is only ${elapsed_seconds}s old; stale threshold is ${STALE_AFTER_SECONDS}s"
fi

exec 8>"$LOCK_FILE"
if "$FLOCK_BIN" -n 8; then
  "$FLOCK_BIN" -u 8 || true
  fail "lock file is not currently held; refusing to stop any unit"
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
  || fail "expected exactly one transient deploy unit for lock holder; found $matching_count"

printf 'STALE_DEPLOY_RECOVERY_TARGET=%s\n' "$matching_unit"
printf 'STALE_DEPLOY_RECOVERY_PID=%s\n' "$holder_pid"
printf 'STALE_DEPLOY_RECOVERY_AGE_SECONDS=%s\n' "$elapsed_seconds"

"$SYSTEMCTL_BIN" stop "$matching_unit"

if ! "$FLOCK_BIN" -w "$LOCK_RELEASE_WAIT_SECONDS" 8; then
  fail "stopped unit but deploy lock was not released within ${LOCK_RELEASE_WAIT_SECONDS}s"
fi
"$FLOCK_BIN" -u 8 || true

if kill -0 "$holder_pid" 2>/dev/null; then
  fail "stopped unit but holder PID is still running"
fi

printf 'STALE_DEPLOY_RECOVERY_OK\n'
printf 'NEXT_ACTION=trigger one signed production recovery request for current main\n'
