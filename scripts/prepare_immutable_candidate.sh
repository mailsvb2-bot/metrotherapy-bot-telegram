#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${1:-${APP_DIR:-/root/metrotherapy}}"
RUNTIME_ROOT="${METRO_RUNTIME_ROOT:-/var/lib/metrotherapy/runtime}"
RELEASES_DIR="${METRO_RELEASES_DIR:-$RUNTIME_ROOT/releases}"
CURRENT_LINK="${METRO_CURRENT_RELEASE_LINK:-$RUNTIME_ROOT/current}"
PREVIOUS_LINK="${METRO_PREVIOUS_RELEASE_LINK:-$RUNTIME_ROOT/previous}"
SYSTEM_PYTHON="${SYSTEM_PYTHON:-/usr/bin/python3}"
RELEASE_MANAGER="${RELEASE_MANAGER:-$SOURCE_DIR/scripts/immutable_release.py}"
ZERO_SHA="0000000000000000000000000000000000000000"

is_valid_sha() {
  case "${1:-}" in
    ''|*[!0-9a-f]*) return 1 ;;
  esac
  [ "${#1}" -eq 40 ] && [ "$1" != "$ZERO_SHA" ]
}

if [ ! -x "$SYSTEM_PYTHON" ] || [ ! -f "$RELEASE_MANAGER" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED candidate preparation tooling is unavailable" >&2
  exit 2
fi

SHA="$(git -C "$SOURCE_DIR" rev-parse HEAD)"
if ! is_valid_sha "$SHA" || ! git -C "$SOURCE_DIR" cat-file -e "$SHA^{commit}" 2>/dev/null; then
  echo "IMMUTABLE_DEPLOY_FAILED candidate preparation SHA is invalid: $SHA" >&2
  exit 3
fi

mkdir -p "$RELEASES_DIR"
RELEASES_CANONICAL="$(readlink -f "$RELEASES_DIR")"
TARGET="$RELEASES_CANONICAL/$SHA"

case "$TARGET" in
  "$RELEASES_CANONICAL"/[0-9a-f][0-9a-f]*) ;;
  *)
    echo "IMMUTABLE_DEPLOY_FAILED candidate path escaped releases root: $TARGET" >&2
    exit 4
    ;;
esac
if [ "$(basename "$TARGET")" != "$SHA" ] || [ "$(dirname "$TARGET")" != "$RELEASES_CANONICAL" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED candidate path is not a canonical direct child: $TARGET" >&2
  exit 5
fi

if [ ! -e "$TARGET" ] && [ ! -L "$TARGET" ]; then
  echo "IMMUTABLE_CANDIDATE_SLOT_EMPTY sha=$SHA path=$TARGET"
  exit 0
fi
if [ -L "$TARGET" ] || [ ! -d "$TARGET" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED candidate target is not a regular release directory: $TARGET" >&2
  exit 6
fi

if "$SYSTEM_PYTHON" "$RELEASE_MANAGER" validate "$TARGET" >/dev/null 2>&1; then
  echo "IMMUTABLE_CANDIDATE_REUSABLE sha=$SHA path=$TARGET"
  exit 0
fi

CURRENT_PATH="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
PREVIOUS_PATH="$(readlink -f "$PREVIOUS_LINK" 2>/dev/null || true)"
if [ "$TARGET" = "$CURRENT_PATH" ] || [ "$TARGET" = "$PREVIOUS_PATH" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED refusing to remove referenced invalid candidate: $TARGET" >&2
  exit 7
fi

rm -rf --one-file-system -- "$TARGET"
if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
  echo "IMMUTABLE_DEPLOY_FAILED invalid candidate removal did not complete: $TARGET" >&2
  exit 8
fi

echo "INVALID_UNREFERENCED_CANDIDATE_REMOVED sha=$SHA path=$TARGET"
