from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.messenger.outbound import SenderRegistry
from services.premium_entitlements import mark_delivery_failed, mark_delivery_sent, pending_delivery


@dataclass(frozen=True)
class PremiumDeliveryRunResult:
    sent: int = 0
    failed: int = 0
    skipped: int = 0


async def flush_premium_delivery_outbox(*, senders: SenderRegistry, limit: int = 20) -> PremiumDeliveryRunResult:
    sent = 0
    failed = 0
    skipped = 0
    for item in pending_delivery(limit=limit):
        delivery_id = int(item["id"])
        platform = str(item["platform"])
        external_user_id = (item.get("external_user_id") or "").strip()
        sender = senders.get(platform)
        if sender is None or not external_user_id:
            skipped += 1
            mark_delivery_failed(delivery_id, f"missing_sender_or_external_user_id:{platform}")
            continue
        try:
            await sender.send_text(external_user_id, str(item["body"]), disable_link_preview=True)
        except Exception as exc:  # validator: allow-wide-except
            failed += 1
            mark_delivery_failed(delivery_id, f"{type(exc).__name__}: {exc}")
            continue
        mark_delivery_sent(delivery_id)
        sent += 1
    return PremiumDeliveryRunResult(sent=sent, failed=failed, skipped=skipped)


class MemorySender:
    """Tiny test/admin helper implementing the Sender protocol."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict[str, Any]]] = []

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any) -> Any:
        self.messages.append((str(external_user_id), text, dict(kwargs)))
        return {"ok": True}
