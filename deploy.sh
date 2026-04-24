#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/root/metrotherapy"
SERVICE_NAME="metrotherapy.service"
PYTHON="$APP_DIR/.venv/bin/python"
PIP="$APP_DIR/.venv/bin/pip"

cd "$APP_DIR"

echo "=== deploy started: $(date -Is) ==="
echo "=== app dir: $APP_DIR ==="

echo "=== git status before ==="
git status --short || true

echo "=== fetch main ==="
git fetch --prune origin main

echo "=== fast-forward only ==="
git merge --ff-only origin/main

if [ -f requirements.txt ]; then
  echo "=== install requirements ==="
  "$PIP" install -r requirements.txt
fi

echo "=== compile smoke ==="
"$PYTHON" -m py_compile main.py app.py services/validators/runtime.py

echo "=== restart service ==="
systemctl restart "$SERVICE_NAME"

echo "=== service status ==="
systemctl status "$SERVICE_NAME" --no-pager -l

echo "=== deploy finished: $(date -Is) ==="
