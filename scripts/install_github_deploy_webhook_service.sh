#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/root/metrotherapy}"
HOOK_SERVICE="${HOOK_SERVICE:-github-deploy-webhook.service}"
HOOK_SOURCE="$APP_DIR/ops/deploy_webhook.py"
HOOK_TARGET="/root/deploy_webhook.py"
UNIT_SOURCE="$APP_DIR/deploy/github-deploy-webhook.service"
UNIT_TARGET="/etc/systemd/system/$HOOK_SERVICE"
ENV_FILE="/etc/metrotherapy/github-deploy-webhook.env"
LEGACY_DROPIN="/etc/systemd/system/$HOOK_SERVICE.d/50-webhook-secret.conf"
LOCAL_URL="${HOOK_LOCAL_URL:-http://127.0.0.1:9001/github-deploy}"
PYTHON_BIN="$APP_DIR/.venv/bin/python"

log() {
  printf '=== %s ===\n' "$*"
}

fail_with_diagnostics() {
  printf 'ERROR: %s\n' "$1" >&2
  systemctl status "$HOOK_SERVICE" --no-pager -l || true
  journalctl -u "$HOOK_SERVICE" -n 120 --no-pager || true
  exit 1
}

if [ "$(id -u)" -ne 0 ]; then
  printf 'ERROR: run this script as root\n' >&2
  exit 1
fi

for command_name in curl install journalctl systemctl; do
  command -v "$command_name" >/dev/null 2>&1 \
    || { printf 'ERROR: required command is missing: %s\n' "$command_name" >&2; exit 1; }
done

[ -x "$PYTHON_BIN" ] || fail_with_diagnostics "production Python is not executable: $PYTHON_BIN"
[ -f "$HOOK_SOURCE" ] || fail_with_diagnostics "webhook source is missing: $HOOK_SOURCE"
[ -f "$UNIT_SOURCE" ] || fail_with_diagnostics "canonical systemd unit is missing: $UNIT_SOURCE"
[ -s "$ENV_FILE" ] || fail_with_diagnostics "webhook environment file is missing or empty: $ENV_FILE"

grep -Eq '^GITHUB_WEBHOOK_SECRET=.+$' "$ENV_FILE" \
  || fail_with_diagnostics "GITHUB_WEBHOOK_SECRET is missing from $ENV_FILE"

log "install canonical webhook runtime and systemd unit"
install -m 0644 "$HOOK_SOURCE" "$HOOK_TARGET"
install -m 0644 "$UNIT_SOURCE" "$UNIT_TARGET"
rm -f "$LEGACY_DROPIN"
systemctl daemon-reload
systemctl enable "$HOOK_SERVICE" >/dev/null
systemctl restart "$HOOK_SERVICE"

log "wait for webhook listener on 127.0.0.1:9001"
ready=0
for attempt in $(seq 1 30); do
  if curl -fsS --max-time 2 "$LOCAL_URL" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [ "$ready" -ne 1 ]; then
  fail_with_diagnostics "webhook did not become ready at $LOCAL_URL"
fi

systemctl is-active --quiet "$HOOK_SERVICE" \
  || fail_with_diagnostics "$HOOK_SERVICE is not active after readiness"

unit_exec="$(systemctl show "$HOOK_SERVICE" -p ExecStart --value)"
printf '%s' "$unit_exec" | grep -F "$PYTHON_BIN" >/dev/null \
  || fail_with_diagnostics "systemd ExecStart does not use $PYTHON_BIN"
printf '%s' "$unit_exec" | grep -F "$HOOK_TARGET" >/dev/null \
  || fail_with_diagnostics "systemd ExecStart does not use $HOOK_TARGET"

printf 'WEBHOOK_SERVICE_OK\n'
printf 'WEBHOOK_SERVICE=%s\n' "$HOOK_SERVICE"
printf 'WEBHOOK_LOCAL_URL=%s\n' "$LOCAL_URL"
printf 'WEBHOOK_PYTHON=%s\n' "$PYTHON_BIN"
