from __future__ import annotations

import os

from services.validators.base import ValidationError
from services.visual_creative_capability import visual_creative_configuration_snapshot


def _prod() -> bool:
    return str(os.getenv("APP_ENV", "dev") or "dev").strip().lower() in {"prod", "production"}


def validate_prod_visual_creative_contract(*, strict: bool = True) -> None:
    """Fail fast when an enabled production creative capability is misconfigured.

    Visual Creative is optional and must not become a dependency of the therapy
    core when disabled. Once enabled, however, startup must enforce the same
    configuration contract as /readyz so a worker cannot boot successfully with
    staff commands that are guaranteed to fail later.
    """

    if not _prod():
        return

    snapshot = visual_creative_configuration_snapshot(app_env="prod")
    if bool(snapshot.get("visual_creative_ready")):
        return

    errors = [
        str(item).strip()
        for item in (snapshot.get("visual_creative_configuration_errors") or [])
        if str(item).strip()
    ]
    reason = ", ".join(errors) if errors else "invalid_configuration"
    if strict:
        raise ValidationError("Production Visual Creative contract failed: " + reason)


__all__ = ["validate_prod_visual_creative_contract"]
