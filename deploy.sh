#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${METROTHERAPY_ENV_FILE:-/etc/metrotherapy/metrotherapy.env}"
BOOTSTRAPPED_SHA="${DEPLOY_BOOTSTRAPPED_SHA:-}"
RECOVERY_SCRIPT="$SOURCE_DIR/scripts/repair_contaminated_current_release.sh"

if [ ! -f "$ENV_FILE" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED production env file is missing: $ENV_FILE" >&2
  exit 2
fi
if [ ! -f "$RECOVERY_SCRIPT" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED current-release recovery script is missing: $RECOVERY_SCRIPT" >&2
  exit 5
fi

export PYTHONDONTWRITEBYTECODE=1
export GIT_TERMINAL_PROMPT=0

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
    bash "$SOURCE_DIR/deploy.sh" "$@"
fi

if [ -n "$BOOTSTRAPPED_SHA" ] && [ "$BOOTSTRAPPED_SHA" != "$AFTER_SHA" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED bootstrap SHA mismatch expected=$BOOTSTRAPPED_SHA actual=$AFTER_SHA" >&2
  exit 4
fi

bash "$RECOVERY_SCRIPT" repair "$SOURCE_DIR"
if bash "$SOURCE_DIR/scripts/immutable_deploy.sh" "$@"; then
  bash "$RECOVERY_SCRIPT" cleanup "$SOURCE_DIR"
else
  code="$?"
  exit "$code"
fi
