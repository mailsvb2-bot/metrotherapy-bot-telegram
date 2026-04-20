#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

APP_USER="${APP_USER:-metrotherapy}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
APP_DIR="${APP_DIR:-/opt/metrotherapy}"
ENV_DIR="${ENV_DIR:-/etc/metrotherapy}"
ENV_FILE="${ENV_FILE:-$ENV_DIR/metrotherapy.env}"
SERVICE_NAME="${SERVICE_NAME:-metrotherapy.service}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_NGINX="${INSTALL_NGINX:-0}"
NGINX_SITES_DIR="${NGINX_SITES_DIR:-/etc/nginx/sites-available}"
NGINX_ENABLED_DIR="${NGINX_ENABLED_DIR:-/etc/nginx/sites-enabled}"
NGINX_SITE_NAME="${NGINX_SITE_NAME:-metrotherapy.conf}"

if ! id -u "$APP_USER" >/dev/null 2>&1; then
  sudo useradd --system --create-home --home-dir /var/lib/metrotherapy --shell /usr/sbin/nologin "$APP_USER"
fi

sudo mkdir -p "$APP_DIR" "$ENV_DIR" /var/lib/metrotherapy /var/log/metrotherapy
sudo chown -R "$APP_USER:$APP_GROUP" /var/lib/metrotherapy /var/log/metrotherapy

rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '*.db' \
  --exclude 'logs/*.log' \
  "$ROOT/" "$APP_DIR/"

if [[ ! -f "$ENV_FILE" ]]; then
  sudo install -m 600 "$ROOT/deploy/metrotherapy.env.example" "$ENV_FILE"
  echo "Created env file template: $ENV_FILE"
fi

sudo "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
sudo "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

sudo install -m 644 "$ROOT/deploy/metrotherapy.service" "$SYSTEMD_DIR/$SERVICE_NAME"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

if [[ "$INSTALL_NGINX" == "1" ]]; then
  sudo install -m 644 "$ROOT/deploy/nginx-metrotherapy.conf" "$NGINX_SITES_DIR/$NGINX_SITE_NAME"
  sudo ln -sf "$NGINX_SITES_DIR/$NGINX_SITE_NAME" "$NGINX_ENABLED_DIR/$NGINX_SITE_NAME"
  sudo nginx -t
  sudo systemctl reload nginx
fi

echo "Server files installed. Edit secrets in: $ENV_FILE"
echo "Then run: sudo systemctl restart $SERVICE_NAME && sudo systemctl status $SERVICE_NAME --no-pager"
