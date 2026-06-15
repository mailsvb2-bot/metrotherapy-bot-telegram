#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/root/metrotherapy"
SERVICE_NAME="metrotherapy.service"
PYTHON="$APP_DIR/.venv/bin/python"
PIP="$APP_DIR/.venv/bin/pip"
LOG_PREFIX="deploy"

cd "$APP_DIR"

echo "=== $LOG_PREFIX started: $(date -Is) ==="
echo "=== app dir: $APP_DIR ==="

OLD_SHA="$(git rev-parse HEAD)"
echo "=== old sha: $OLD_SHA ==="

rollback() {
  code="$?"
  echo "=== deploy failed with code=$code at $(date -Is) ==="
  echo "=== rollback to $OLD_SHA ==="
  git reset --hard "$OLD_SHA" || true
  systemctl restart "$SERVICE_NAME" || true
  systemctl status "$SERVICE_NAME" --no-pager -l || true
  exit "$code"
}
trap rollback ERR

echo "=== git status before ==="
git status --short

if [ -n "$(git status --short)" ]; then
  echo "ERROR: dirty working tree; refusing deploy"
  exit 10
fi

echo "=== fetch origin/main ==="
git fetch --prune origin main

echo "=== fast-forward only ==="
git merge --ff-only origin/main

NEW_SHA="$(git rev-parse HEAD)"
echo "=== new sha: $NEW_SHA ==="

if [ -f requirements.txt ]; then
  echo "=== install requirements ==="
  "$PIP" install -r requirements.txt
fi

echo "=== compile smoke ==="
"$PYTHON" -m compileall \
  main.py \
  app.py \
  runtime \
  services \
  handlers \
  keyboards \
  scripts

echo "=== prod validator ==="
VALIDATOR_RELEASE_MODE=1 VALIDATOR_GUARDRAILS_STRICT=1 "$PYTHON" scripts/validate_project.py

if [ -f scripts/check_ruff.py ]; then
  echo "=== ruff/project quality check ==="
  "$PYTHON" scripts/check_ruff.py
fi

echo "=== restart service ==="
systemctl restart "$SERVICE_NAME"

echo "=== wait service ==="
sleep 8

echo "=== service status ==="
systemctl is-active --quiet "$SERVICE_NAME"
systemctl status "$SERVICE_NAME" --no-pager -l | sed -n '1,60p'

echo "=== local health ==="
curl -fsS --max-time 10 http://127.0.0.1:8082/healthz >/dev/null

echo "=== public health ==="
curl -fsS --max-time 15 https://metrotherapy-bot.metrotherapy.ru/healthz >/dev/null

if [ -f scripts/post_deploy_verify.py ]; then
  echo "=== post deploy verify ==="
  "$PYTHON" scripts/post_deploy_verify.py --skip-pytest
fi

trap - ERR
echo "=== deploy finished OK: $(date -Is) ==="
echo "=== deployed sha: $NEW_SHA ==="
