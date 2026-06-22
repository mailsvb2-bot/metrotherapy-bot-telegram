#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_NAME="${SERVICE_NAME:-metrotherapy.service}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
VENV_PATH="${VENV_PATH:-$ROOT/.venv}"
INSTALL_SERVICE="${INSTALL_SERVICE:-0}"
ENV_FILE="${ENV_FILE:-}"

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

"$PYTHON_BIN" -m venv "$VENV_PATH"
source "$VENV_PATH/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt

export APP_ENV="${APP_ENV:-prod}"
export VALIDATOR_RELEASE_MODE=1
python scripts/validate_project.py

echo "Validation OK"
echo "Run after service start: ENV_FILE=/etc/metrotherapy/metrotherapy.env bash deploy/post_deploy_smoke.sh"

if [[ "$INSTALL_SERVICE" == "1" ]]; then
  sudo cp "$ROOT/deploy/metrotherapy.service" "$SYSTEMD_DIR/$SERVICE_NAME"
  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
  echo "Service installed and restarted: $SERVICE_NAME"
else
  echo "Service file not installed automatically. Set INSTALL_SERVICE=1 to enable systemd installation."
fi
