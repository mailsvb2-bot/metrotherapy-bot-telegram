from __future__ import annotations

import re

_SAFE_CODE_RE = re.compile(r"[^a-z0-9_.:-]+")


class MessengerTransportError(RuntimeError):
    """Base error for outbound messenger provider transport failures."""

    def __init__(self, message: str = "messenger_transport_error", *, code: str | None = None) -> None:
        super().__init__(str(message or "messenger_transport_error"))
        raw_code = str(code or "").strip().casefold()
        normalized = _SAFE_CODE_RE.sub("_", raw_code).strip("_")
        self.safe_code = normalized[:120] or type(self).__name__


class MessengerMediaNotReadyError(MessengerTransportError):
    """Raised when a provider accepts media upload but cannot send it yet."""


class MessengerMediaTokenRejectedError(MessengerTransportError):
    """Raised when a cached provider media capability must be invalidated and rebuilt."""


def safe_transport_error_text(exc: BaseException) -> str:
    """Return a bounded error label that never includes provider payloads or credentials."""

    code = str(getattr(exc, "safe_code", "") or "").strip()
    if code:
        return f"{type(exc).__name__}:{code}"[:180]
    return type(exc).__name__[:180]
