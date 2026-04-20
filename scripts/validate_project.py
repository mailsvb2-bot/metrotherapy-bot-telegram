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
if os.getenv("VALIDATOR_RELEASE_MODE") == "1" and not os.getenv("METRO_DB_PATH"):
    import tempfile
    _tmp_db = Path(tempfile.gettempdir()) / "metro_validator_data.db"
    os.environ["METRO_DB_PATH"] = str(_tmp_db)


# In release validation mode, use dummy secrets for import-time prod fail-fast checks.
# This keeps preflight hermetic while still forcing real deployments to provide env vars.
if os.getenv("VALIDATOR_RELEASE_MODE") == "1":
    os.environ.setdefault("BOT_TOKEN", "000000:VALIDATION")
    os.environ.setdefault("PAY_PROVIDER_TOKEN", "000000:VALIDATION")

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
