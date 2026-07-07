from __future__ import annotations

import sys
import os
import logging
from pathlib import Path

# Ensure project root (..../v16) is on sys.path even when running:
#   python scripts/validate_project.py
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# During validation, never create/ship a real SQLite DB inside the repo.
# Use a temporary DB outside the project tree so release hygiene checks stay stable.
if os.getenv("VALIDATOR_RELEASE_MODE") == "1":
    import tempfile
    _tmp_dir = Path(tempfile.mkdtemp(prefix="metro_validator_"))
    if not os.getenv("METRO_DB_PATH"):
        os.environ["METRO_DB_PATH"] = str(_tmp_dir / "validator.db")
    os.environ.setdefault("LOG_PATH", str(_tmp_dir / "validator_app.log"))
    os.environ.setdefault("STORE_LOG_PATH", str(_tmp_dir / "validator_store.log"))


# In release validation mode, use dummy identity/payment contract values for
# import-time prod fail-fast checks. This keeps preflight hermetic while still
# forcing real deployments to provide their own env vars.
if os.getenv("VALIDATOR_RELEASE_MODE") == "1":
    os.environ.setdefault("BOT_TOKEN", "000000:VALIDATION")
    os.environ.setdefault("ADMIN_IDS", "1")
    os.environ.setdefault("YOOKASSA_SHOP_ID", "validation-shop")
    os.environ.setdefault("YOOKASSA_SECRET_KEY", "validation-key")
    os.environ.setdefault("PAYMENT_CHECKOUT_SIGNING_KEY", "validation-checkout-key")
    os.environ.setdefault("YOOKASSA_WEBHOOK_SECRET", "validation-webhook-key")
    os.environ.setdefault("PAYMENT_PUBLIC_BASE_URL", "https://metrotherapy.example")

# In release validation mode, prevent creation of __pycache__ / .pyc during imports.
if os.getenv("VALIDATOR_RELEASE_MODE") == "1":
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

from core.logging import setup_logging
setup_logging()

from services.schema import init_db
from services.validator import validate_all, ValidationError

log = logging.getLogger(__name__)


def main() -> int:
    try:

        if os.getenv("VALIDATOR_RELEASE_MODE") == "1":
            # Some environments may still create __pycache__/pyc despite dont_write_bytecode.
            # Clean them up so release hygiene checks reflect the archive contents.
            for d in ROOT.rglob("__pycache__"):
                if d.is_dir():
                    import shutil
                    shutil.rmtree(d, ignore_errors=True)
            for f in list(ROOT.rglob("*.pyc")) + list(ROOT.rglob("*.pyo")):
                try:
                    f.unlink()
                except OSError:
                    pass

        init_db()

        # Local dev UX: allow running validation even if a stateful DB exists in the repo.
        # Release hygiene should still be strict in CI / release mode.
        strict = os.getenv("VALIDATOR_STRICT", "0").strip().lower() in {"1", "true", "yes", "on"}
        if os.getenv("VALIDATOR_RELEASE_MODE") == "1":
            strict = True

        validate_all(strict=strict)
        log.info("✅ Validation OK")
        return 0
    except ValidationError as e:
        log.error("❌ Validation failed: %s", e)
        return 2
    except (OSError, RuntimeError, ValueError, TypeError, ImportError):  # validator: allow-wide-except
        log.exception("❌ Unexpected error during validation")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
