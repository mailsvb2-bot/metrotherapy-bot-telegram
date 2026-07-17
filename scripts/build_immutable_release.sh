#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${SOURCE_DIR:-/root/metrotherapy}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/var/lib/metrotherapy/runtime}"
RELEASES_DIR="${RELEASES_DIR:-$RUNTIME_ROOT/releases}"
SYSTEM_PYTHON="${SYSTEM_PYTHON:-/usr/bin/python3}"
PIP_BOOTSTRAP_VERSION="${PIP_BOOTSTRAP_VERSION:-26.1.2}"
SHA="${1:-${RELEASE_SHA:-}}"

is_valid_sha() {
  case "${1:-}" in
    ''|*[!0-9a-f]*) return 1 ;;
  esac
  [ "${#1}" -eq 40 ]
}

if ! is_valid_sha "$SHA"; then
  echo "BUILD_IMMUTABLE_RELEASE_FAILED invalid SHA" >&2
  exit 2
fi
if [ ! -x "$SYSTEM_PYTHON" ]; then
  echo "BUILD_IMMUTABLE_RELEASE_FAILED Python is unavailable: $SYSTEM_PYTHON" >&2
  exit 3
fi
if ! git -C "$SOURCE_DIR" cat-file -e "$SHA^{commit}" 2>/dev/null; then
  echo "BUILD_IMMUTABLE_RELEASE_FAILED commit is unavailable: $SHA" >&2
  exit 4
fi

mkdir -p "$RELEASES_DIR"
FINAL_DIR="$RELEASES_DIR/$SHA"
MANAGER="$SOURCE_DIR/scripts/immutable_release.py"

if [ -f "$FINAL_DIR/.release.json" ]; then
  "$SYSTEM_PYTHON" "$MANAGER" validate "$FINAL_DIR" >/dev/null
  echo "IMMUTABLE_RELEASE_REUSED sha=$SHA path=$FINAL_DIR"
  exit 0
fi
if [ -e "$FINAL_DIR" ]; then
  echo "BUILD_IMMUTABLE_RELEASE_FAILED incomplete release already exists: $FINAL_DIR" >&2
  exit 5
fi

BUILD_DIR="$(mktemp -d "$RELEASES_DIR/.build-${SHA}.XXXXXX")"
cleanup() {
  rm -rf "$BUILD_DIR"
}
trap cleanup EXIT TERM INT HUP

# The source snapshot is detached from the mutable production worktree.
git -C "$SOURCE_DIR" archive --format=tar "$SHA" | tar -xf - -C "$BUILD_DIR"

"$SYSTEM_PYTHON" -m venv "$BUILD_DIR/.venv"
RELEASE_PYTHON="$BUILD_DIR/.venv/bin/python"
RELEASE_PIP="$BUILD_DIR/.venv/bin/pip"
"$RELEASE_PYTHON" -m pip install --disable-pip-version-check "pip==$PIP_BOOTSTRAP_VERSION"

PY_MINOR="$($RELEASE_PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PY_MINOR" in
  3.12) LOCK_FILE="requirements.txt" ;;
  3.13) LOCK_FILE="requirements-py313.txt" ;;
  *)
    echo "BUILD_IMMUTABLE_RELEASE_FAILED unsupported Python: $PY_MINOR" >&2
    exit 6
    ;;
esac
if [ ! -f "$BUILD_DIR/$LOCK_FILE" ]; then
  echo "BUILD_IMMUTABLE_RELEASE_FAILED dependency lock is missing: $LOCK_FILE" >&2
  exit 7
fi

"$RELEASE_PIP" install --require-hashes -r "$BUILD_DIR/$LOCK_FILE"

PYTHONDONTWRITEBYTECODE=0 "$RELEASE_PYTHON" -m compileall -q \
  "$BUILD_DIR/main.py" \
  "$BUILD_DIR/app.py" \
  "$BUILD_DIR/runtime" \
  "$BUILD_DIR/services" \
  "$BUILD_DIR/handlers" \
  "$BUILD_DIR/keyboards" \
  "$BUILD_DIR/scripts"

LOCK_SHA256="$(sha256sum "$BUILD_DIR/$LOCK_FILE" | awk '{print $1}')"
BUILT_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$BUILD_DIR/.release.json" <<EOF
{
  "sha": "$SHA",
  "built_at_utc": "$BUILT_AT_UTC",
  "python_version": "$PY_MINOR",
  "pip_bootstrap_version": "$PIP_BOOTSTRAP_VERSION",
  "lock_file": "$LOCK_FILE",
  "lock_sha256": "$LOCK_SHA256"
}
EOF

chmod 0444 "$BUILD_DIR/.release.json"
# A rename inside one filesystem is the publication boundary. No incomplete
# directory ever appears under releases/<sha>.
mv "$BUILD_DIR" "$FINAL_DIR"
trap - EXIT TERM INT HUP

"$SYSTEM_PYTHON" "$MANAGER" validate "$FINAL_DIR" >/dev/null
echo "IMMUTABLE_RELEASE_BUILT sha=$SHA path=$FINAL_DIR lock=$LOCK_FILE lock_sha256=$LOCK_SHA256"
