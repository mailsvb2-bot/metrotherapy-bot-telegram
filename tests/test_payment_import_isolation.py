from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAYMENTS_INIT = ROOT / "services" / "payments" / "__init__.py"
HANDLERS_INIT = ROOT / "handlers" / "__init__.py"


def _isolated_import_snapshot(module_name: str) -> dict[str, bool]:
    watched = (
        "services.db",
        "services.gift_claims",
        "services.payments.telegram_stars",
        "services.payments.stars_invoice_transport",
    )
    code = (
        "import json, os, sys; "
        f"sys.path.insert(0, {str(ROOT)!r}); "
        "os.environ['APP_ENV']='prod'; "
        "os.environ['PAYMENT_RECEIPT_EMAIL']='refund-proof@metrotherapy.local'; "
        f"import {module_name}; "
        f"print(json.dumps({{name: name in sys.modules for name in {watched!r}}}, sort_keys=True))"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip())


def test_payments_package_import_has_no_cross_provider_side_effects() -> None:
    snapshot = _isolated_import_snapshot("services.payments")

    assert snapshot == {
        "services.db": False,
        "services.gift_claims": False,
        "services.payments.stars_invoice_transport": False,
        "services.payments.telegram_stars": False,
    }


def test_yookassa_checkout_import_does_not_bootstrap_stars_or_gift_db() -> None:
    snapshot = _isolated_import_snapshot("services.payments.yookassa_checkout")

    assert snapshot == {
        "services.db": False,
        "services.gift_claims": False,
        "services.payments.stars_invoice_transport": False,
        "services.payments.telegram_stars": False,
    }


def test_yookassa_receipt_build_is_leaf_safe_in_production_mode() -> None:
    code = (
        "import os, sys; "
        f"sys.path.insert(0, {str(ROOT)!r}); "
        "os.environ['APP_ENV']='prod'; "
        "os.environ['PAYMENT_RECEIPT_EMAIL']='refund-proof@metrotherapy.local'; "
        "from services.payments.yookassa_checkout import build_yookassa_receipt; "
        "r=build_yookassa_receipt(amount_value='2499.00', description='Metrotherapy'); "
        "assert r['customer']['email']=='refund-proof@metrotherapy.local'; "
        "assert r['items'][0]['amount']['value']=='2499.00'"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", code],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr


def test_telegram_transport_wiring_lives_at_handler_boundary() -> None:
    payments_source = PAYMENTS_INIT.read_text(encoding="utf-8")
    handlers_source = HANDLERS_INIT.read_text(encoding="utf-8")

    assert "install_stars_invoice_link_transport()" not in payments_source
    assert "from services.payments.stars_invoice_transport import install_stars_invoice_link_transport" in handlers_source
    assert "install_stars_invoice_link_transport()" in handlers_source
