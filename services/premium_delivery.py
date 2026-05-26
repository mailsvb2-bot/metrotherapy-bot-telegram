from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.premium_entitlements import mark_delivery_failed, mark_delivery_sent, pending_delivery


@dataclass(frozen=True)
class PremiumDeliveryRunResult:
    sent: int = 0
    failed: int = 0
    skipped: int = 0


class MemorySender:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict[str, Any]]] = []

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any) -> Any:
        self.messages.append((str(external_user_id), text, dict(kwargs)))
        return {"ok": True}


async def flush_premium_delivery_outbox(*, senders: Any, limit: int = 20) -> PremiumDeliveryRunResult:
    sent = 0
    failed = 0
    skipped = 0
    for item in pending_delivery(limit=limit):
        delivery_id = int(item["id"])
        platform = str(item["platform"])
        external_user_id = (item.get("external_user_id") or "").strip()
        sender = senders.get(platform) if hasattr(senders, "get") else None
        if sender is None or not external_user_id:
            skipped += 1
            mark_delivery_failed(delivery_id, f"missing_sender_or_external_user_id:{platform}")
            continue
        try:
            await sender.send_text(external_user_id, str(item["body"]), disable_link_preview=True)
        except RuntimeError as exc:
            failed += 1
            mark_delivery_failed(delivery_id, f"RuntimeError: {exc}")
            continue
        except OSError as exc:
            failed += 1
            mark_delivery_failed(delivery_id, f"OSError: {exc}")
            continue
        mark_delivery_sent(delivery_id)
        sent += 1
    return PremiumDeliveryRunResult(sent=sent, failed=failed, skipped=skipped)
