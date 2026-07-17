from __future__ import annotations

import logging

from services.db import get_connection
from services.privacy_manifest import validate_privacy_manifest
from services.validators.base import ValidationError

log = logging.getLogger(__name__)


def validate_privacy_schema(strict: bool = True) -> None:
    with get_connection() as conn:
        try:
            report = validate_privacy_manifest(conn, strict=True)
        except RuntimeError as exc:
            if strict:
                raise ValidationError(str(exc)) from exc
            log.warning("Privacy manifest warning: %s", exc)
            return
    log.info(
        "Privacy manifest OK: user_owned_tables=%s",
        len(report.discovered_user_tables),
    )
