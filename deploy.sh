#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${METROTHERAPY_ENV_FILE:-/etc/metrotherapy/metrotherapy.env}"
if [ ! -f "$ENV_FILE" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED production env file is missing: $ENV_FILE" >&2
  exit 2
fi
export PYTHONDONTWRITEBYTECODE=1
bash "$SOURCE_DIR/scripts/check_remote_main_topology.sh" "$SOURCE_DIR"
exec bash "$SOURCE_DIR/scripts/immutable_deploy.sh" "$@"
