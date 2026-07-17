from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from services.payments.reconciliation import ReconciliationResult, record_yookassa_webhook
from services.payments.yookassa_provider import (
    YooKassaProviderVerificationError,
    verify_yookassa_refund_webhook_with_provider,
    verify_yookassa_webhook_with_provider,
)
from services.payments.yookassa_refunds import record_yookassa_refund


def _object(payload: dict[str, Any]) -> dict[str, Any]:
    obj = payload.get("object")
    return obj if isinstance(obj, dict) else {}


def _metadata(obj: dict[str, Any]) -> dict[str, Any]:
    meta = obj.get("metadata")
    return dict(meta) if isinstance(meta, dict) else {}


def _amount_minor(obj: dict[str, Any]) -> int:
    amount = obj.get("amount")
    if not isinstance(amount, dict):
        return 0
    raw = str(amount.get("value") or "0").replace(",", ".").strip()
    try:
        value = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return 0
    return max(0, int(value * 100))


def _metadata_user_id(meta: dict[str, Any]) -> int:
    for key in ("external_user_id", "user_id", "telegram_user_id"):
        raw = str(meta.get(key) or "").strip()
        if not raw:
            continue
        try:
            parsed = int(raw, 10)
        except ValueError:
            continue
        if str(parsed) == raw and parsed != 0:
            return parsed
    return 0


def _enqueue_growth_conversion(**kwargs: Any) -> None:
    """Lazy boundary: payment webhook import must not depend on Growth modules."""

    try:
        from services.growth_conversion_hub import record_payment_conversion_dry_run_safe
    except ImportError:
        return
    record_payment_conversion_dry_run_safe(**kwargs)


def _record_verified_conversion_dry_run(payload: dict[str, Any], result: ReconciliationResult) -> None:
    if not result.ok or not result.side_effects_done:
        return
    obj = _object(payload)
    status = str(obj.get("status") or result.status or "").strip().lower()
    event = str(payload.get("event") or result.event or "").strip().lower()
    if event != "payment.succeeded" and status != "succeeded":
        return

    meta = _metadata(obj)
    amount_value = obj.get("amount")
    amount: dict[str, Any] = amount_value if isinstance(amount_value, dict) else {}
    payment_id = str(obj.get("id") or result.provider_payment_id or "").strip()
    if not payment_id:
        return
    gift_token = str(meta.get("gift_token") or "").strip()
    attribution = {
        key: meta.get(key)
        for key in (
            "source",
            "campaign",
            "creative",
            "utm_source",
            "utm_campaign",
            "utm_creative",
            "utm_content",
            "ad_spend",
        )
        if meta.get(key) not in (None, "")
    }
    _enqueue_growth_conversion(
        source_platform="yookassa",
        source_event=event or status or "payment.succeeded",
        external_event_id=payment_id,
        user_id=_metadata_user_id(meta),
        amount_minor=_amount_minor(obj),
        currency=str(amount.get("currency") or "RUB"),
        gift=bool(gift_token),
        attribution=attribution,
        payload={
            "kind": str(meta.get("kind") or "payment"),
            "package_id": str(meta.get("package_id") or ""),
            "provider_status": status,
            "gift_token_present": bool(gift_token),
        },
    )


def _canonical_provider_event(status: str) -> str:
    normalized = str(status or "unknown").strip().lower() or "unknown"
    aliases = {"cancelled": "canceled"}
    return f"payment.{aliases.get(normalized, normalized)}"


def _provider_canonical_payload(payload: dict[str, Any], provider_object: dict[str, Any] | None) -> dict[str, Any]:
    if provider_object is None:
        return payload
    canonical = dict(payload)
    canonical["object"] = dict(provider_object)
    canonical["event"] = _canonical_provider_event(str(provider_object.get("status") or "unknown"))
    return canonical


def _provider_canonical_refund_payload(
    payload: dict[str, Any], provider_object: dict[str, Any] | None
) -> dict[str, Any]:
    if provider_object is None:
        return payload
    canonical = dict(payload)
    canonical["object"] = dict(provider_object)
    canonical["event"] = "refund.succeeded"
    return canonical


def _verification_failure(payload: dict[str, Any], exc: Exception) -> ReconciliationResult:
    obj = _object(payload)
    event = str(payload.get("event") or "").strip()
    is_refund = event.casefold() == "refund.succeeded"
    payment_id = str(
        obj.get("payment_id") if is_refund else obj.get("id") or payload.get("id") or ""
    ).strip()
    status = str(obj.get("status") or "unknown").strip() or "unknown"
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


def record_verified_yookassa_webhook(payload: dict[str, Any]) -> ReconciliationResult:
    """Verify YooKassa source-of-truth before persisting payment or refund facts."""

    event = str(payload.get("event") or "").strip().casefold()
    if event == "refund.succeeded":
        try:
            provider_refund = verify_yookassa_refund_webhook_with_provider(payload)
        except YooKassaProviderVerificationError as exc:
            return _verification_failure(payload, exc)
        canonical_refund = _provider_canonical_refund_payload(payload, provider_refund)
        return record_yookassa_refund(canonical_refund)

    try:
        provider_object = verify_yookassa_webhook_with_provider(payload)
    except YooKassaProviderVerificationError as exc:
        return _verification_failure(payload, exc)

    canonical_payload = _provider_canonical_payload(payload, provider_object)
    result = record_yookassa_webhook(canonical_payload)
    _record_verified_conversion_dry_run(canonical_payload, result)
    return result
