#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-}"
SERVICE_NAME="${SERVICE_NAME:-metrotherapy.service}"
VENV_PATH="${VENV_PATH:-$ROOT/.venv}"

if [[ -n "$ENV_FILE" ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "ENV_FILE not found: $ENV_FILE" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

if command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files | grep -q "^${SERVICE_NAME}"; then
  systemctl is-active --quiet "$SERVICE_NAME"
fi

source "$VENV_PATH/bin/activate"
python scripts/prod_readiness_check.py
python scripts/smoke_runtime.py

if [[ "${MESSENGER_WEBHOOK_ENABLED:-0}" == "1" || "${TELEGRAM_TRANSPORT:-polling}" == "webhook" || "${TELEGRAM_WEBHOOK_ENABLED:-0}" == "1" ]]; then
  python - <<'PY'
import os, urllib.request
host = (os.getenv('MESSENGER_WEBHOOK_HOST', '127.0.0.1') or '127.0.0.1').strip()
port = int(os.getenv('MESSENGER_WEBHOOK_PORT', '8081') or 8081)
url = f'http://{host}:{port}/'
with urllib.request.urlopen(url, timeout=5) as response:
    print(f'webhook port reachable: {response.status}')
PY
fi

if [[ "${RESTORE_DRILL:-0}" == "1" ]]; then
  python scripts/restore_drill.py
fi
