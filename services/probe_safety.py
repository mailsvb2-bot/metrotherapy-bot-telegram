from __future__ import annotations

"""Shared safety primitives for synthetic probes that can mutate live storage."""

import uuid

from services.probe_ledger import (
    SYNTHETIC_USER_ID_MAX,
    SYNTHETIC_USER_ID_MIN,
    assert_synthetic_user_id,
)


class ProbeMutationAuthorizationRequired(RuntimeError):
    """Raised before any DB access when a mutating probe was not authorized."""


class ProbeInvariantError(RuntimeError):
    """Raised when a synthetic probe observes a broken production invariant."""


def require_live_db_mutation(allowed: bool) -> None:
    if not bool(allowed):
        raise ProbeMutationAuthorizationRequired("live_db_mutation_authorization_required")


def new_synthetic_user_id() -> int:
    namespace_size = int(SYNTHETIC_USER_ID_MAX) - int(SYNTHETIC_USER_ID_MIN) + 1
    offset = int(uuid.uuid4().hex[:12], 16) % namespace_size
    user_id = int(SYNTHETIC_USER_ID_MAX) - offset
    assert_synthetic_user_id(user_id)
    return user_id


def safe_probe_error_code(exc: BaseException) -> str:
    return f"probe_failure:{type(exc).__name__}"
