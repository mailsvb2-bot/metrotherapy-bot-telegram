#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-metrotherapy.service}"
RUNTIME_ROOT="${METRO_RUNTIME_ROOT:-/var/lib/metrotherapy/runtime}"
STATE_ROOT="${METRO_WRITABLE_ROOT:-$(dirname "$RUNTIME_ROOT")/state}"
DROPIN="${METRO_RUNTIME_WRITE_GUARD_OVERRIDE:-/etc/systemd/system/$SERVICE_NAME.d/zzz-runtime-write-guard.conf}"
SYSTEMCTL="${SYSTEMCTL:-/usr/bin/systemctl}"

for required in "$RUNTIME_ROOT" "$STATE_ROOT" "$(dirname "$DROPIN")"; do
  [ -n "$required" ] || {
    echo "RUNTIME_WRITE_GUARD_FAILED empty required path" >&2
    exit 10
  }
done

case "$STATE_ROOT" in
  "$RUNTIME_ROOT"|"$RUNTIME_ROOT"/*)
    echo "RUNTIME_WRITE_GUARD_FAILED writable state must be outside immutable runtime: $STATE_ROOT" >&2
    exit 11
    ;;
esac

mkdir -p \
  "$STATE_ROOT/data" \
  "$STATE_ROOT/logs" \
  "$STATE_ROOT/python-cache" \
  "$STATE_ROOT/xdg-cache" \
  "$STATE_ROOT/matplotlib" \
  "$STATE_ROOT/tmp" \
  "$(dirname "$DROPIN")"
chmod 0750 \
  "$STATE_ROOT" \
  "$STATE_ROOT/data" \
  "$STATE_ROOT/logs" \
  "$STATE_ROOT/python-cache" \
  "$STATE_ROOT/xdg-cache" \
  "$STATE_ROOT/matplotlib" \
  "$STATE_ROOT/tmp"

temp="$(mktemp "$(dirname "$DROPIN")/.runtime-write-guard.XXXXXX")"
cleanup() {
  rm -f "$temp"
}
trap cleanup EXIT TERM INT HUP

cat > "$temp" <<EOF
[Service]
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONPYCACHEPREFIX=$STATE_ROOT/python-cache
Environment=XDG_CACHE_HOME=$STATE_ROOT/xdg-cache
Environment=MPLCONFIGDIR=$STATE_ROOT/matplotlib
Environment=TMPDIR=$STATE_ROOT/tmp
Environment=METRO_WRITABLE_ROOT=$STATE_ROOT
Environment=METRO_DATA_DIR=$STATE_ROOT/data
Environment=METRO_LOGS_DIR=$STATE_ROOT/logs
ReadOnlyPaths=$RUNTIME_ROOT
ReadWritePaths=$STATE_ROOT
EOF
chmod 0644 "$temp"

if [ ! -f "$DROPIN" ] || ! cmp -s "$temp" "$DROPIN"; then
  mv -f "$temp" "$DROPIN"
  trap - EXIT TERM INT HUP
  "$SYSTEMCTL" daemon-reload
  echo "RUNTIME_WRITE_GUARD_INSTALLED dropin=$DROPIN runtime=$RUNTIME_ROOT state=$STATE_ROOT"
else
  "$SYSTEMCTL" daemon-reload
  echo "RUNTIME_WRITE_GUARD_OK dropin=$DROPIN runtime=$RUNTIME_ROOT state=$STATE_ROOT"
fi
