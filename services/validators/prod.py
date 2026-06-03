from __future__ import annotations

import os

from services.validators.base import ValidationError


def validate_prod_guardrails(*, strict: bool = True) -> None:
    """Fail closed when production starts without release architecture guardrails.

    The app already has a production config fail-fast, but release validation and
    architecture checks used to depend on optional environment flags. In prod this
    must be an explicit deployment contract, not a README recommendation.

    Emergency bypass exists only for manual recovery and is intentionally noisy.
    """
    app_env = (os.getenv("APP_ENV", "dev") or "dev").strip().lower()
    if app_env not in {"prod", "production"}:
        return

    if os.getenv("ALLOW_UNGUARDED_PROD", "").strip().lower() in {"1", "true", "yes", "on"}:
        return

    missing: list[str] = []
    if os.getenv("VALIDATOR_RELEASE_MODE", "").strip().lower() not in {"1", "true", "yes", "on"}:
        missing.append("VALIDATOR_RELEASE_MODE=1")
    if os.getenv("VALIDATOR_GUARDRAILS_STRICT", "").strip().lower() not in {"1", "true", "yes", "on"}:
        missing.append("VALIDATOR_GUARDRAILS_STRICT=1")

    if missing:
        msg = "Production requires release guardrails: " + ", ".join(missing)
        if strict:
            raise ValidationError(msg)
