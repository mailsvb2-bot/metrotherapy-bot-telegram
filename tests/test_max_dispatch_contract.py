from __future__ import annotations

from services.messenger.reply_dispatcher import _canonical_payment_text


def test_max_dispatch_keeps_stateful_text() -> None:
    text = "STATEFUL SURFACE\n\nRecipient: user\n\nready"
    assert _canonical_payment_text("max", 1001, "mx1001", text) == text
