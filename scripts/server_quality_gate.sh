#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_DIR="${LOG_DIR:-/tmp/metrotherapy_quality_logs}"
VENV_DIR="${VENV_DIR:-/tmp/metrotherapy_quality_venv}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
mkdir -p "$LOG_DIR"

run_step() {
  local name="$1"
  shift
  echo "==> $name"
  set +e
  "$@" >"$LOG_DIR/${name}.log" 2>&1
  local code=$?
  set -e
  if [ "$code" -ne 0 ]; then
    echo "FAIL: $name exit=$code"
    echo "--- tail $LOG_DIR/${name}.log ---"
    tail -n 80 "$LOG_DIR/${name}.log" || true
    echo "--- end tail ---"
    return "$code"
  fi
  echo "OK: $name"
}

if [ ! -d "$VENV_DIR" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python -m pip install --upgrade pip >"$LOG_DIR/pip_upgrade.log" 2>&1
python -m pip install -r requirements-dev.txt >"$LOG_DIR/pip_install.log" 2>&1

export PYTHONDONTWRITEBYTECODE=1
export VALIDATOR_SKIP_AUDIO=1

failures=0
run_step release_hygiene_before python scripts/check_release_hygiene.py || failures=$((failures+1))
run_step compile_project python -m compileall services scripts handlers core runtime config app.py main.py || failures=$((failures+1))

APP_ENV=prod \
VALIDATOR_RELEASE_MODE=1 \
VALIDATOR_GUARDRAILS_STRICT=1 \
ADMIN_IDS=1 \
YOOKASSA_SHOP_ID=server-check \
YOOKASSA_SECRET_KEY=server-check \
PAYMENT_CHECKOUT_SIGNING_KEY=server-check \
YOOKASSA_WEBHOOK_SECRET=server-check \
PAYMENT_PUBLIC_BASE_URL=https://metrotherapy.example \
run_step smoke_sqlite python scripts/smoke.py || failures=$((failures+1))

APP_ENV=test LOAD_DOTENV=0 run_step pytest python -m pytest -q -p no:cacheprovider || failures=$((failures+1))

APP_ENV=prod \
VALIDATOR_RELEASE_MODE=1 \
VALIDATOR_GUARDRAILS_STRICT=1 \
ADMIN_IDS=1 \
YOOKASSA_SHOP_ID=server-check \
YOOKASSA_SECRET_KEY=server-check \
PAYMENT_CHECKOUT_SIGNING_KEY=server-check \
YOOKASSA_WEBHOOK_SECRET=server-check \
PAYMENT_PUBLIC_BASE_URL=https://metrotherapy.example \
run_step strict_validation python scripts/validate_project.py || failures=$((failures+1))

run_step ruff python scripts/check_ruff.py || failures=$((failures+1))
run_step mypy_payment_privacy python -m mypy services/payments/checkout_intent.py services/payments/yookassa_provider.py services/payments/verified_reconciliation.py runtime/payment_http.py services/privacy_controls.py || failures=$((failures+1))
run_step bandit_payment_privacy python -m bandit -q -c pyproject.toml services/payments runtime/payment_http.py services/privacy_controls.py || failures=$((failures+1))
run_step pip_audit python -m pip_audit -r requirements.txt --progress-spinner off || failures=$((failures+1))
run_step release_hygiene_after python scripts/check_release_hygiene.py || failures=$((failures+1))

if [ -n "${DATABASE_URL:-}" ]; then
  APP_ENV=prod \
  METRO_DB_ENGINE=postgres \
  VALIDATOR_RELEASE_MODE=1 \
  VALIDATOR_GUARDRAILS_STRICT=1 \
  ADMIN_IDS=1 \
  YOOKASSA_SHOP_ID=server-check \
  YOOKASSA_SECRET_KEY=server-check \
  PAYMENT_CHECKOUT_SIGNING_KEY=server-check \
  YOOKASSA_WEBHOOK_SECRET=server-check \
  PAYMENT_PUBLIC_BASE_URL=https://metrotherapy.example \
  run_step smoke_postgres python scripts/smoke.py || failures=$((failures+1))
else
  echo "SKIP: smoke_postgres because DATABASE_URL is empty"
fi

if [ "$failures" -ne 0 ]; then
  echo "QUALITY GATE FAILED: $failures step(s). Logs: $LOG_DIR"
  exit 2
fi

echo "QUALITY GATE OK. Logs: $LOG_DIR"
