#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${SOURCE_DIR:-/root/metrotherapy}"
RUNTIME_ROOT="${RUNTIME_ROOT:-/var/lib/metrotherapy/runtime}"
RELEASES_DIR="${RELEASES_DIR:-$RUNTIME_ROOT/releases}"
SYSTEM_PYTHON="${SYSTEM_PYTHON:-/usr/bin/python3}"
AUDIO_SOURCE_DIR="${AUDIO_SOURCE_DIR:-$SOURCE_DIR/audio}"
LEGACY_SHARED_AUDIO_DIR="${LEGACY_SHARED_AUDIO_DIR:-${SHARED_AUDIO_DIR:-$(dirname "$RUNTIME_ROOT")/audio}}"
AUDIO_RELEASES_DIR="${AUDIO_RELEASES_DIR:-$(dirname "$RUNTIME_ROOT")/audio-releases}"
AUDIO_OWNER="${AUDIO_OWNER:-root}"
APP_GROUP="${APP_GROUP:-metrotherapy}"
REQUIRE_VERSIONED_AUDIO="${REQUIRE_VERSIONED_AUDIO:-1}"
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
AUDIO_MANAGER="$SOURCE_DIR/services/audio_asset_integrity.py"

if [ ! -f "$AUDIO_MANAGER" ]; then
  echo "BUILD_IMMUTABLE_RELEASE_FAILED audio integrity manager is missing: $AUDIO_MANAGER" >&2
  exit 5
fi

if [ -f "$FINAL_DIR/.release.json" ]; then
  "$SYSTEM_PYTHON" "$MANAGER" validate "$FINAL_DIR" >/dev/null
  if [ "$REQUIRE_VERSIONED_AUDIO" = "1" ]; then
    "$SYSTEM_PYTHON" "$AUDIO_MANAGER" validate-release "$FINAL_DIR" --require-versioned >/dev/null
  else
    "$SYSTEM_PYTHON" "$AUDIO_MANAGER" validate-release "$FINAL_DIR" >/dev/null
  fi
  echo "IMMUTABLE_RELEASE_REUSED sha=$SHA path=$FINAL_DIR"
  exit 0
fi
if [ -e "$FINAL_DIR" ]; then
  echo "BUILD_IMMUTABLE_RELEASE_FAILED incomplete release already exists: $FINAL_DIR" >&2
  exit 6
fi

BUILD_DIR="$(mktemp -d "$RELEASES_DIR/.build-${SHA}.XXXXXX")"
AUDIO_BUILD_DIR=""
cleanup() {
  rm -rf "$BUILD_DIR"
  if [ -n "$AUDIO_BUILD_DIR" ]; then
    rm -rf "$AUDIO_BUILD_DIR"
  fi
}
trap cleanup EXIT TERM INT HUP
chmod 0755 "$BUILD_DIR"

# The source snapshot is detached from the mutable production worktree.
git -C "$SOURCE_DIR" archive --format=tar "$SHA" | tar -xf - -C "$BUILD_DIR"

# Resolve the authoritative media source. Legacy shared storage is accepted only
# as an input for migration; every new release points to an immutable versioned
# asset directory named by its actual content digest.
if [ ! -d "$AUDIO_SOURCE_DIR" ] && [ -d "$LEGACY_SHARED_AUDIO_DIR" ]; then
  AUDIO_SOURCE_DIR="$LEGACY_SHARED_AUDIO_DIR"
fi

AUDIO_ASSET_DIR=""
AUDIO_ASSET_SHA256=""
AUDIO_ASSET_FILE_COUNT=0
if [ -d "$AUDIO_SOURCE_DIR" ]; then
  mkdir -p "$AUDIO_RELEASES_DIR"
  chmod 0750 "$AUDIO_RELEASES_DIR"
  AUDIO_BUILD_DIR="$(mktemp -d "$AUDIO_RELEASES_DIR/.build-audio.XXXXXX")"
  chmod 0750 "$AUDIO_BUILD_DIR"

  # Staging begins empty on every build, so deleted or renamed tracks can never
  # survive from an older asset set. Nested symlinks and special files are
  # rejected by the integrity manager before publication.
  cp -a "$AUDIO_SOURCE_DIR/." "$AUDIO_BUILD_DIR/"
  find "$AUDIO_BUILD_DIR" -type d -exec chmod 0750 {} +
  find "$AUDIO_BUILD_DIR" -type f -exec chmod 0640 {} +

  AUDIO_ASSET_JSON="$("$SYSTEM_PYTHON" "$AUDIO_MANAGER" seal "$AUDIO_BUILD_DIR")"
  AUDIO_ASSET_SHA256="$(printf '%s' "$AUDIO_ASSET_JSON" | "$SYSTEM_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["asset_sha256"])')"
  AUDIO_ASSET_FILE_COUNT="$(printf '%s' "$AUDIO_ASSET_JSON" | "$SYSTEM_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["file_count"])')"
  AUDIO_ASSET_DIR="$AUDIO_RELEASES_DIR/$AUDIO_ASSET_SHA256"

  if [ -d "$AUDIO_ASSET_DIR" ]; then
    "$SYSTEM_PYTHON" "$AUDIO_MANAGER" validate-dir "$AUDIO_ASSET_DIR" \
      --expected-sha256 "$AUDIO_ASSET_SHA256" >/dev/null
    rm -rf "$AUDIO_BUILD_DIR"
    AUDIO_BUILD_DIR=""
  elif [ -e "$AUDIO_ASSET_DIR" ]; then
    echo "BUILD_IMMUTABLE_RELEASE_FAILED audio asset target is not a directory: $AUDIO_ASSET_DIR" >&2
    exit 7
  else
    mv "$AUDIO_BUILD_DIR" "$AUDIO_ASSET_DIR"
    AUDIO_BUILD_DIR=""
    chown -R "$AUDIO_OWNER:$APP_GROUP" "$AUDIO_ASSET_DIR"
  fi

  rm -rf "$BUILD_DIR/audio"
  ln -s "$AUDIO_ASSET_DIR" "$BUILD_DIR/audio"
  "$SYSTEM_PYTHON" "$AUDIO_MANAGER" write-release-pointer \
    --release-dir "$BUILD_DIR" \
    --asset-dir "$AUDIO_ASSET_DIR" >/dev/null
elif [ "$REQUIRE_VERSIONED_AUDIO" = "1" ]; then
  echo "BUILD_IMMUTABLE_RELEASE_FAILED audio source is unavailable: $AUDIO_SOURCE_DIR" >&2
  exit 8
fi

"$SYSTEM_PYTHON" -m venv "$BUILD_DIR/.venv"
RELEASE_PYTHON="$BUILD_DIR/.venv/bin/python"
RELEASE_PIP="$BUILD_DIR/.venv/bin/pip"
PIP_VERSION="$($RELEASE_PYTHON -m pip --version | awk '{print $2}')"

PY_MINOR="$($RELEASE_PYTHON -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PY_MINOR" in
  3.12) LOCK_FILE="requirements.txt" ;;
  3.13) LOCK_FILE="requirements-py313.txt" ;;
  *)
    echo "BUILD_IMMUTABLE_RELEASE_FAILED unsupported Python: $PY_MINOR" >&2
    exit 9
    ;;
esac
if [ ! -f "$BUILD_DIR/$LOCK_FILE" ]; then
  echo "BUILD_IMMUTABLE_RELEASE_FAILED dependency lock is missing: $LOCK_FILE" >&2
  exit 10
fi

"$RELEASE_PIP" install --no-compile --require-hashes -r "$BUILD_DIR/$LOCK_FILE"
# Remove any bytecode created by ensurepip/pip. Production runs with
# PYTHONDONTWRITEBYTECODE=1, so release contents remain sealed after publication.
find "$BUILD_DIR" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "$BUILD_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

# Venv console scripts and pyvenv.cfg are generated with the temporary build
# path. Rewrite text launchers to the deterministic final release path before
# calculating the release tree digest. Symlinks are intentionally excluded so
# this step can never follow `.venv/bin/python` into the system interpreter.
BUILD_DIR_VALUE="$BUILD_DIR" FINAL_DIR_VALUE="$FINAL_DIR" "$SYSTEM_PYTHON" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path

old = os.environ["BUILD_DIR_VALUE"].encode()
new = os.environ["FINAL_DIR_VALUE"].encode()
venv = Path(os.environ["BUILD_DIR_VALUE"]) / ".venv"
launchers = [
    path
    for path in (venv / "bin").iterdir()
    if path.is_file() and not path.is_symlink()
]
for path in [venv / "pyvenv.cfg", *launchers]:
    data = path.read_bytes()
    if old in data:
        path.write_bytes(data.replace(old, new))
PY

# Compile project code with deterministic final co_filename values. Third-party
# packages stay source-only to avoid temporary-path bytecode in the sealed venv.
PYTHONDONTWRITEBYTECODE=0 "$RELEASE_PYTHON" -m compileall -q \
  -s "$BUILD_DIR" -p "$FINAL_DIR" \
  "$BUILD_DIR/main.py" \
  "$BUILD_DIR/app.py" \
  "$BUILD_DIR/runtime" \
  "$BUILD_DIR/services" \
  "$BUILD_DIR/handlers" \
  "$BUILD_DIR/keyboards" \
  "$BUILD_DIR/scripts"

LOCK_SHA256="$(sha256sum "$BUILD_DIR/$LOCK_FILE" | awk '{print $1}')"
TREE_SHA256="$($SYSTEM_PYTHON "$MANAGER" tree-digest "$BUILD_DIR" | "$SYSTEM_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["tree_sha256"])')"
BUILT_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
cat > "$BUILD_DIR/.release.json" <<EOF
{
  "sha": "$SHA",
  "built_at_utc": "$BUILT_AT_UTC",
  "python_version": "$PY_MINOR",
  "pip_version": "$PIP_VERSION",
  "lock_file": "$LOCK_FILE",
  "lock_sha256": "$LOCK_SHA256",
  "tree_sha256": "$TREE_SHA256",
  "shared_audio_dir": "$AUDIO_ASSET_DIR",
  "audio_asset_sha256": "$AUDIO_ASSET_SHA256",
  "audio_asset_file_count": $AUDIO_ASSET_FILE_COUNT
}
EOF

chmod 0444 "$BUILD_DIR/.release.json"
# A rename inside one filesystem is the publication boundary. No incomplete
# directory ever appears under releases/<sha>.
mv "$BUILD_DIR" "$FINAL_DIR"
trap - EXIT TERM INT HUP

"$SYSTEM_PYTHON" "$MANAGER" validate "$FINAL_DIR" >/dev/null
if [ "$REQUIRE_VERSIONED_AUDIO" = "1" ]; then
  "$SYSTEM_PYTHON" "$AUDIO_MANAGER" validate-release "$FINAL_DIR" --require-versioned >/dev/null
else
  "$SYSTEM_PYTHON" "$AUDIO_MANAGER" validate-release "$FINAL_DIR" >/dev/null
fi
echo "IMMUTABLE_RELEASE_BUILT sha=$SHA path=$FINAL_DIR lock=$LOCK_FILE lock_sha256=$LOCK_SHA256 tree_sha256=$TREE_SHA256 audio_sha256=$AUDIO_ASSET_SHA256 audio_files=$AUDIO_ASSET_FILE_COUNT"
