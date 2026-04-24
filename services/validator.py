from __future__ import annotations
import os
import logging
import sqlite3

log = logging.getLogger(__name__)

"""Project validators.

Split into smaller modules under services/validators to reduce regression risk.
Public API remains in this module for backward compatibility.
"""

from services.validators.base import ValidationError
from services.validators.db import (
    validate_no_real_db,
    validate_db_schema,
    validate_schema_decomposition,
)
from services.validators.audio import (
    validate_demo_audio,
    validate_full_audio,
)
from services.validators.runtime import (
    validate_background_tasks,
    validate_single_scheduler,
    validate_wide_except_policy,
)
from services.validators.release import (
    validate_release_hygiene,
    validate_compileall,
)
from services.validators.architecture import validate_architecture_contracts

def _row_scalar(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    try:
        return row[0]
    except (TypeError, KeyError, IndexError):
        return None


def _row_price_sample(row) -> str:
    if isinstance(row, dict):
        return f"{row.get('code')}={row.get('price')}"
    return f"{row[0]}={row[2]}"


def validate_all(strict: bool = True) -> None:
    """Run project validators.

    Notes:
    - In dev a stateful SQLite DB file is expected (created on first run), so we do not
      run release-hygiene checks by default.
    - In prod/CI/release we enforce hygiene via strict=True or VALIDATOR_RELEASE_MODE=1.
    """

    release_mode = os.getenv("VALIDATOR_RELEASE_MODE", "0").strip().lower() in {"1", "true", "yes", "on"}

    validate_demo_audio(strict=strict)
    validate_full_audio(strict=strict)
    validate_db_schema(strict=strict)
    validate_background_tasks(strict=strict)

    guardrails_strict = os.getenv("VALIDATOR_GUARDRAILS_STRICT", "0").strip().lower() in {"1", "true", "yes", "on"}
    validate_single_scheduler(strict=guardrails_strict)
    validate_schema_decomposition(strict=guardrails_strict)

    if release_mode:
        validate_no_real_db(strict=True)
        validate_release_hygiene(strict=True)
        validate_compileall(strict=True)
        validate_wide_except_policy(strict=True)
        validate_architecture_contracts(strict=True)
    elif guardrails_strict:
        validate_architecture_contracts(strict=True)

    try:
        from pathlib import Path
        from services.db import DB_PATH, get_connection
        from services.db.runtime import CONFIG, redacted_db_target

        db_path = Path(DB_PATH).resolve()
        size = db_path.stat().st_size if CONFIG.uses_sqlite and db_path.exists() else 0

        with get_connection() as conn:
            try:
                cnt = int(_row_scalar(conn.execute("SELECT COUNT(*) FROM plans").fetchone()) or -1)
            except Exception:  # validator: allow-wide-except
                cnt = -1
            try:
                sample = conn.execute(
                    "SELECT code, title, price, updated_at, is_active FROM plans ORDER BY is_active DESC, code LIMIT 6"
                ).fetchall()
                sample_str = ", ".join(_row_price_sample(r) for r in (sample or [])) if sample else "-"
            except Exception:  # validator: allow-wide-except
                sample_str = "-"

        log.info(
            "DB in use: %s (engine=%s, size=%s bytes), plans=%s, sample=%s",
            redacted_db_target(),
            CONFIG.engine,
            size,
            cnt,
            sample_str,
        )
    except (OSError, sqlite3.Error):
        log.exception("Failed to print DB diagnostics")
    except Exception:  # validator: allow-wide-except
        log.exception("Failed to print DB diagnostics")

    log.info("Startup validation OK")
