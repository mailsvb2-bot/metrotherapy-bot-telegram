from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _base_prod_env() -> dict[str, str]:
    env = {
        "APP_ENV": "prod",
        "LOAD_DOTENV": "0",
        "PYTHONPATH": str(ROOT),
        "BOT_TOKEN": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        "ADMIN_IDS": "1",
        "YOOKASSA_SHOP_ID": "shop",
        "YOOKASSA_SECRET_KEY": "secret",
        "PAYMENT_CHECKOUT_SIGNING_KEY": "checkout-secret",
        "YOOKASSA_WEBHOOK_SECRET": "webhook-secret",
        "PAYMENT_PUBLIC_BASE_URL": "https://metrotherapy.example",
        "TELEGRAM_TRANSPORT": "polling",
        "TELEGRAM_WEBHOOK_ENABLED": "0",
        "MESSENGER_WEBHOOK_ENABLED": "0",
    }
    return {**os.environ, **env}


def _import_settings(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "import config.settings; print('ok')"],
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_prod_settings_do_not_require_legacy_telegram_payment_token():
    env = _base_prod_env()
    env.pop("PAY_PROVIDER_TOKEN", None)

    proc = _import_settings(env)

    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout


def test_prod_settings_reject_dangerous_payment_override_without_drill_flag():
    env = _base_prod_env()
    env["ALLOW_UNVERIFIED_YOOKASSA_WEBHOOK_IN_PROD"] = "1"
    env.pop("PAYMENT_DANGEROUS_OVERRIDES_ALLOWED", None)

    proc = _import_settings(env)

    assert proc.returncode != 0
    assert "Dangerous payment override" in (proc.stderr + proc.stdout)
