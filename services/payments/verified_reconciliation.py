from __future__ import annotations

from services.payments.reconciliation import ReconciliationResult, record_yookassa_webhook
from services.payments.yookassa_provider import (
    YooKassaProviderVerificationError,
    verify_yookassa_webhook_with_provider,
)


def record_verified_yookassa_webhook(payload: dict) -> ReconciliationResult:
    """Verify YooKassa source-of-truth before grant-producing reconciliation.

    This wrapper is used by the public HTTP webhook. Direct reconciliation remains
    available for hermetic probes/tests that intentionally construct local payloads
    without provider calls.
    """
    try:
        verify_yookassa_webhook_with_provider(payload)
    except YooKassaProviderVerificationError as exc:
        obj = payload.get("object") if isinstance(payload.get("object"), dict) else {}
        payment_id = str(obj.get("id") or payload.get("id") or "").strip()
        status = str(obj.get("status") or "unknown").strip() or "unknown"
        event = str(payload.get("event") or "").strip()
        return ReconciliationResult(
            ok=False,
            provider="yookassa",
            provider_payment_id=payment_id,
            status=status,
            event=event,
            inserted=False,
            problem=f"provider_verification_failed:{exc}",
            processing_status="action_required",
            side_effects_done=False,
        )
    return record_yookassa_webhook(payload)
