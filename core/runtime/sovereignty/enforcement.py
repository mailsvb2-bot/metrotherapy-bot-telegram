from __future__ import annotations

import contextvars
import inspect
import secrets
from typing import Optional

from core.ai.decision_types import DecisionToken
from core.runtime.sovereignty.safe_mode import SAFE_MODE
from core.runtime.sovereignty.incident_log import log_incident


class ArchitecturalViolation(RuntimeError):
    pass


_RUNTIME_SIGNATURE: Optional[str] = None

# Current token in execution context (set by ActionGateway)
_current_token: contextvars.ContextVar[Optional[DecisionToken]] = contextvars.ContextVar("decision_token", default=None)


def bind_signature(signature: str) -> None:
    global _RUNTIME_SIGNATURE
    if _RUNTIME_SIGNATURE is not None and _RUNTIME_SIGNATURE != signature:
        incident_id = log_incident(
            "ARCH_VIOLATION",
            where="sovereignty.bind_signature",
            message="ARCH_VIOLATION: signature rebind attempted",
            context={"prev": _RUNTIME_SIGNATURE, "new": signature},
        )
        SAFE_MODE.enable("ARCH_VIOLATION: signature rebind", incident_id=incident_id)
        raise ArchitecturalViolation("ARCH_VIOLATION: signature rebind attempted")
    _RUNTIME_SIGNATURE = signature


def new_runtime_signature() -> str:
    return secrets.token_hex(16)


def set_current_token(token: Optional[DecisionToken]) -> None:
    _current_token.set(token)


def get_current_token() -> Optional[DecisionToken]:
    return _current_token.get()


def require_token(token: Optional[DecisionToken]) -> None:
    if token is None:
        _bypass("INVALID_TOKEN", "missing token")
    if _RUNTIME_SIGNATURE is None:
        _bypass("ARCH_VIOLATION", "runtime signature not bound")
    if token.signature != _RUNTIME_SIGNATURE:
        _bypass("INVALID_TOKEN", "signature mismatch")
    if token.is_expired():
        _bypass("INVALID_TOKEN", "token expired")


def arch_violation(message: str, code: str = "DECISION_BYPASS") -> None:
    _bypass(code, message)


def _bypass(code: str, message: str) -> None:
    frame = inspect.stack()[2]
    where = f"{frame.filename}:{frame.lineno}:{frame.function}"
    incident_id = log_incident(code, where=where, message=message, context={})
    SAFE_MODE.enable(f"{code}: {message}", incident_id=incident_id)
    raise ArchitecturalViolation(f"{code}: {message}")
