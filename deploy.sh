#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$SOURCE_DIR/scripts/check_remote_main_topology.sh" "$SOURCE_DIR"
exec bash "$SOURCE_DIR/scripts/immutable_deploy.sh" "$@"
