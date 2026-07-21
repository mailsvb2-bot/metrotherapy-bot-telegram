#!/usr/bin/env bash
set -Eeuo pipefail

MODE="${1:-repair}"
SOURCE_DIR="${2:-${APP_DIR:-/root/metrotherapy}}"
RUNTIME_ROOT="${METRO_RUNTIME_ROOT:-/var/lib/metrotherapy/runtime}"
RELEASES_DIR="${METRO_RELEASES_DIR:-$RUNTIME_ROOT/releases}"
CURRENT_LINK="${METRO_CURRENT_RELEASE_LINK:-$RUNTIME_ROOT/current}"
PREVIOUS_LINK="${METRO_PREVIOUS_RELEASE_LINK:-$RUNTIME_ROOT/previous}"
DEPLOY_STATE_DIR="${DEPLOY_STATE_DIR:-/var/lib/metrotherapy/deploy-state}"
DEPLOYED_SHA_FILE="${DEPLOYED_SHA_FILE:-$DEPLOY_STATE_DIR/deployed_sha}"
RECOVERY_RELEASES_ROOT="${METRO_RECOVERY_RELEASES_ROOT:-$RUNTIME_ROOT/recovery-releases}"
RECOVERY_STATE_DIR="${METRO_RECOVERY_STATE_DIR:-$DEPLOY_STATE_DIR/contaminated-releases}"
SYSTEM_PYTHON="${SYSTEM_PYTHON:-/usr/bin/python3}"
RELEASE_MANAGER="${RELEASE_MANAGER:-$SOURCE_DIR/scripts/immutable_release.py}"
RELEASE_BUILDER="${RELEASE_BUILDER:-$SOURCE_DIR/scripts/build_immutable_release.sh}"
TIMEOUT_BIN="${TIMEOUT_BIN:-/usr/bin/timeout}"
RELEASE_BUILD_TIMEOUT_SECONDS="${RELEASE_BUILD_TIMEOUT_SECONDS:-1200}"
ZERO_SHA="0000000000000000000000000000000000000000"

is_valid_sha() {
  case "${1:-}" in
    ''|*[!0-9a-f]*) return 1 ;;
  esac
  [ "${#1}" -eq 40 ] && [ "$1" != "$ZERO_SHA" ]
}

is_positive_integer() {
  case "${1:-}" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "$1" -gt 0 ]
}

validate_release() {
  "$SYSTEM_PYTHON" "$RELEASE_MANAGER" validate "$1" >/dev/null 2>&1
}

is_safe_release_path() {
  local path="$1"
  local parent name
  [ -n "$path" ] || return 1
  parent="$(dirname "$path")"
  name="$(basename "$path")"
  is_valid_sha "$name" || return 1
  if [ "$parent" = "$RELEASES_DIR" ]; then
    return 0
  fi
  case "$parent" in
    "$RECOVERY_RELEASES_ROOT"/generation-*) return 0 ;;
  esac
  return 1
}

atomic_point_current_to() {
  local target="$1"
  local temp_link="$RUNTIME_ROOT/.current-repair.$$.link"
  rm -f "$temp_link"
  ln -s "$target" "$temp_link"
  mv -Tf "$temp_link" "$CURRENT_LINK"
}

record_contaminated_path() {
  local sha="$1"
  local original_path="$2"
  local state_file temp
  mkdir -p "$RECOVERY_STATE_DIR"
  state_file="$RECOVERY_STATE_DIR/${sha}-$(date +%s)-$$.state"
  temp="${state_file}.tmp"
  printf '%s\n%s\n' "$sha" "$original_path" > "$temp"
  chmod 0600 "$temp"
  mv -f "$temp" "$state_file"
  printf '%s\n' "$state_file"
}

repair_current() {
  local current_path sha recorded_sha="" previous_path=""
  local generation_dir recovery_dir state_file

  if [ ! -L "$CURRENT_LINK" ]; then
    echo "CURRENT_RELEASE_RECOVERY_SKIPPED reason=current_link_missing"
    return 0
  fi

  current_path="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
  [ -n "$current_path" ] && [ -d "$current_path" ] || {
    echo "CURRENT_RELEASE_RECOVERY_FAILED current link is unresolved: $CURRENT_LINK" >&2
    return 20
  }

  is_safe_release_path "$current_path" || {
    echo "CURRENT_RELEASE_RECOVERY_FAILED unsafe current release path: $current_path" >&2
    return 21
  }

  sha="$(basename "$current_path")"
  git -C "$SOURCE_DIR" cat-file -e "$sha^{commit}" 2>/dev/null || {
    echo "CURRENT_RELEASE_RECOVERY_FAILED release commit is unavailable: $sha" >&2
    return 22
  }

  if [ -f "$DEPLOYED_SHA_FILE" ]; then
    IFS= read -r recorded_sha < "$DEPLOYED_SHA_FILE" || true
  fi

  if is_valid_sha "$recorded_sha" && [ "$recorded_sha" != "$sha" ]; then
    previous_path="$(readlink -f "$PREVIOUS_LINK" 2>/dev/null || true)"
    if [ -n "$previous_path" ] \
      && [ -d "$previous_path" ] \
      && is_safe_release_path "$previous_path" \
      && [ "$(basename "$previous_path")" = "$recorded_sha" ] \
      && validate_release "$previous_path"; then
      atomic_point_current_to "$previous_path"
      if ! "$SYSTEM_PYTHON" "$RELEASE_MANAGER" inspect "$CURRENT_LINK" --required >/dev/null 2>&1; then
        atomic_point_current_to "$current_path" || true
        echo "CURRENT_RELEASE_RECOVERY_FAILED deployed rollback target did not validate" >&2
        return 26
      fi
      echo "CURRENT_RELEASE_ROLLBACK_RESCUED failed_current=$sha deployed=$recorded_sha target=$previous_path"
      return 0
    fi
    echo "CURRENT_RELEASE_RECOVERY_FAILED deployed marker mismatch current=$sha recorded=$recorded_sha" >&2
    return 23
  fi

  if validate_release "$current_path"; then
    echo "CURRENT_RELEASE_INTEGRITY_OK path=$current_path"
    return 0
  fi

  mkdir -p "$RECOVERY_RELEASES_ROOT" "$DEPLOY_STATE_DIR"
  generation_dir="$RECOVERY_RELEASES_ROOT/generation-$(date +%s)-$$"
  recovery_dir="$generation_dir/$sha"
  mkdir -p "$generation_dir"

  echo "=== rebuild clean rollback release sha=$sha source=$current_path target=$recovery_dir ==="
  "$TIMEOUT_BIN" --signal=TERM --kill-after=30s "$RELEASE_BUILD_TIMEOUT_SECONDS" \
    env SOURCE_DIR="$SOURCE_DIR" RUNTIME_ROOT="$RUNTIME_ROOT" RELEASES_DIR="$generation_dir" \
      SYSTEM_PYTHON="$SYSTEM_PYTHON" SHARED_AUDIO_DIR="${SHARED_AUDIO_DIR:-$SOURCE_DIR/audio}" \
      bash "$RELEASE_BUILDER" "$sha"
  validate_release "$recovery_dir" || {
    echo "CURRENT_RELEASE_RECOVERY_FAILED clean rollback release did not validate: $recovery_dir" >&2
    rm -rf --one-file-system "$generation_dir"
    return 24
  }

  state_file="$(record_contaminated_path "$sha" "$current_path")"
  atomic_point_current_to "$recovery_dir"
  if ! "$SYSTEM_PYTHON" "$RELEASE_MANAGER" inspect "$CURRENT_LINK" --required >/dev/null 2>&1; then
    atomic_point_current_to "$current_path" || true
    rm -f "$state_file"
    rm -rf --one-file-system "$generation_dir"
    echo "CURRENT_RELEASE_RECOVERY_FAILED repaired current link did not validate" >&2
    return 25
  fi

  echo "CURRENT_RELEASE_RECOVERY_READY sha=$sha original=$current_path recovery=$recovery_dir"
}

cleanup_state_records() {
  local active_current active_previous state_file sha original_path
  active_current="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
  active_previous="$(readlink -f "$PREVIOUS_LINK" 2>/dev/null || true)"
  [ -d "$RECOVERY_STATE_DIR" ] || return 0

  while IFS= read -r state_file; do
    [ -n "$state_file" ] || continue
    sha="$(sed -n '1p' "$state_file" 2>/dev/null || true)"
    original_path="$(sed -n '2p' "$state_file" 2>/dev/null || true)"
    if ! is_valid_sha "$sha" || ! is_safe_release_path "$original_path" || [ "$(basename "$original_path")" != "$sha" ]; then
      echo "CURRENT_RELEASE_RECOVERY_CLEANUP_SKIPPED malformed_state=$state_file" >&2
      continue
    fi
    if [ "$original_path" != "$active_current" ] && [ "$original_path" != "$active_previous" ]; then
      if [ -e "$original_path" ]; then
        rm -rf --one-file-system "$original_path"
        echo "CONTAMINATED_RELEASE_REMOVED path=$original_path"
      fi
      rm -f "$state_file"
    fi
  done < <(find "$RECOVERY_STATE_DIR" -maxdepth 1 -type f -name '*.state' -print | sort)
}

cleanup_recovery_generations() {
  local active_current active_previous release_dir generation_dir name
  active_current="$(readlink -f "$CURRENT_LINK" 2>/dev/null || true)"
  active_previous="$(readlink -f "$PREVIOUS_LINK" 2>/dev/null || true)"
  [ -d "$RECOVERY_RELEASES_ROOT" ] || return 0

  while IFS= read -r release_dir; do
    [ -n "$release_dir" ] || continue
    name="$(basename "$release_dir")"
    is_valid_sha "$name" || continue
    if [ "$release_dir" != "$active_current" ] && [ "$release_dir" != "$active_previous" ]; then
      rm -rf --one-file-system "$release_dir"
      echo "UNREFERENCED_RECOVERY_RELEASE_REMOVED path=$release_dir"
    fi
  done < <(find "$RECOVERY_RELEASES_ROOT" -mindepth 2 -maxdepth 2 -type d -print | sort)

  while IFS= read -r generation_dir; do
    [ -n "$generation_dir" ] || continue
    rmdir "$generation_dir" 2>/dev/null || true
  done < <(find "$RECOVERY_RELEASES_ROOT" -mindepth 1 -maxdepth 1 -type d -name 'generation-*' -print | sort)
}

cleanup_recovery() {
  cleanup_state_records
  cleanup_recovery_generations
  rmdir "$RECOVERY_STATE_DIR" 2>/dev/null || true
  rmdir "$RECOVERY_RELEASES_ROOT" 2>/dev/null || true
  echo "CURRENT_RELEASE_RECOVERY_CLEANUP_OK"
}

if [ ! -x "$SYSTEM_PYTHON" ] || [ ! -x "$TIMEOUT_BIN" ]; then
  echo "CURRENT_RELEASE_RECOVERY_FAILED required executable is unavailable" >&2
  exit 10
fi
if [ ! -f "$RELEASE_MANAGER" ] || [ ! -f "$RELEASE_BUILDER" ]; then
  echo "CURRENT_RELEASE_RECOVERY_FAILED release tooling is unavailable" >&2
  exit 11
fi
if ! is_positive_integer "$RELEASE_BUILD_TIMEOUT_SECONDS"; then
  echo "CURRENT_RELEASE_RECOVERY_FAILED invalid build timeout" >&2
  exit 12
fi

case "$MODE" in
  repair) repair_current ;;
  cleanup) cleanup_recovery ;;
  *)
    echo "usage: $0 {repair|cleanup} [source_dir]" >&2
    exit 2
    ;;
esac
