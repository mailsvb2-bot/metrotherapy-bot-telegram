#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${METROTHERAPY_ENV_FILE:-/etc/metrotherapy/metrotherapy.env}"
BOOTSTRAPPED_SHA="${DEPLOY_BOOTSTRAPPED_SHA:-}"
RECOVERY_SCRIPT="$SOURCE_DIR/scripts/repair_contaminated_current_release.sh"
CANDIDATE_PREPARER="$SOURCE_DIR/scripts/prepare_immutable_candidate.sh"
WRITE_GUARD_SCRIPT="$SOURCE_DIR/scripts/install_runtime_write_guard.sh"
RUNTIME_ROOT="${METRO_RUNTIME_ROOT:-/var/lib/metrotherapy/runtime}"
CURRENT_LINK="${METRO_CURRENT_RELEASE_LINK:-$RUNTIME_ROOT/current}"
STATE_ROOT="${METRO_WRITABLE_ROOT:-$(dirname "$RUNTIME_ROOT")/state}"
SERVICE_NAME="${SERVICE_NAME:-metrotherapy.service}"

if [ ! -f "$ENV_FILE" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED production env file is missing: $ENV_FILE" >&2
  exit 2
fi
if [ ! -f "$RECOVERY_SCRIPT" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED current-release recovery script is missing: $RECOVERY_SCRIPT" >&2
  exit 5
fi
if [ ! -f "$CANDIDATE_PREPARER" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED candidate preparation script is missing: $CANDIDATE_PREPARER" >&2
  exit 6
fi
if [ ! -f "$WRITE_GUARD_SCRIPT" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED runtime write guard is missing: $WRITE_GUARD_SCRIPT" >&2
  exit 7
fi

mkdir -p \
  "$STATE_ROOT/python-cache" \
  "$STATE_ROOT/xdg-cache" \
  "$STATE_ROOT/matplotlib" \
  "$STATE_ROOT/tmp"
export METRO_WRITABLE_ROOT="$STATE_ROOT"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="$STATE_ROOT/python-cache"
export XDG_CACHE_HOME="$STATE_ROOT/xdg-cache"
export MPLCONFIGDIR="$STATE_ROOT/matplotlib"
export TMPDIR="$STATE_ROOT/tmp"
export GIT_TERMINAL_PROMPT=0

restore_runtime_after_failure() {
  local recovered_release=""
  if ! bash "$RECOVERY_SCRIPT" repair "$SOURCE_DIR"; then
    echo "IMMUTABLE_DEPLOY_RECOVERY_FAILED current release repair failed" >&2
    return 1
  fi
  recovered_release="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
  [ -n "$recovered_release" ] && [ -d "$recovered_release" ] || {
    echo "IMMUTABLE_DEPLOY_RECOVERY_FAILED current release is unresolved after repair" >&2
    return 1
  }
  if ! bash "$WRITE_GUARD_SCRIPT" for-release "$recovered_release"; then
    echo "IMMUTABLE_DEPLOY_RECOVERY_FAILED compatible runtime guard selection failed" >&2
    return 1
  fi
  /usr/bin/systemctl restart "$SERVICE_NAME"
}

bash "$SOURCE_DIR/scripts/check_remote_main_topology.sh" "$SOURCE_DIR"

git -C "$SOURCE_DIR" checkout main
BEFORE_SHA="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
git -C "$SOURCE_DIR" fetch --prune origin main
git -C "$SOURCE_DIR" merge --ff-only origin/main
AFTER_SHA="$(git -C "$SOURCE_DIR" rev-parse HEAD)"

if [ "$BEFORE_SHA" != "$AFTER_SHA" ]; then
  if [ "$BOOTSTRAPPED_SHA" = "$AFTER_SHA" ]; then
    echo "IMMUTABLE_DEPLOY_FAILED deploy wrapper self-reexec loop at $AFTER_SHA" >&2
    exit 3
  fi
  echo "=== deploy wrapper updated old=$BEFORE_SHA new=$AFTER_SHA; re-exec updated wrapper ==="
  exec env \
    DEPLOY_BOOTSTRAPPED_SHA="$AFTER_SHA" \
    METROTHERAPY_ENV_FILE="$ENV_FILE" \
    METRO_RUNTIME_ROOT="$RUNTIME_ROOT" \
    METRO_CURRENT_RELEASE_LINK="$CURRENT_LINK" \
    METRO_WRITABLE_ROOT="$STATE_ROOT" \
    bash "$SOURCE_DIR/deploy.sh" "$@"
fi

if [ -n "$BOOTSTRAPPED_SHA" ] && [ "$BOOTSTRAPPED_SHA" != "$AFTER_SHA" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED bootstrap SHA mismatch expected=$BOOTSTRAPPED_SHA actual=$AFTER_SHA" >&2
  exit 4
fi

bash "$WRITE_GUARD_SCRIPT" enforce
bash "$RECOVERY_SCRIPT" repair "$SOURCE_DIR"
bash "$CANDIDATE_PREPARER" "$SOURCE_DIR"
if bash "$SOURCE_DIR/scripts/immutable_deploy.sh" "$@"; then
  bash "$RECOVERY_SCRIPT" cleanup "$SOURCE_DIR"
else
  code="$?"
  restore_runtime_after_failure || true
  exit "$code"
fi
