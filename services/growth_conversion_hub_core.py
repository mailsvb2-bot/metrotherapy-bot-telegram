from __future__ import annotations

import hashlib
import json
from typing import Any

DRY_RUN_MODE = "dry_run"
DRY_RUN_STATUS = "planned"
DISPATCH_ALLOWED = False

_ALLOWED_CONVERSION_TYPES = frozenset({
    "demo_ack",
    "tariff_open",
    "payment_success",
    "gift_paid",
})

_CONVERSION_ALIASES = {
    "invoice_paid": "payment_success",
    "paid": "payment_success",
    "payment.succeeded": "payment_success",
    "gift_payment": "gift_paid",
}

_ATTRIBUTION_KEYS = (
    "source",
    "campaign",
    "creative",
    "utm_source",
    "utm_campaign",
    "utm_creative",
    "utm_content",
    "ad_spend",
    "payload",
)


def _clean(value: Any, *, limit: int = 160) -> str:
    text = str(value or "").strip().replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    return text[:limit]


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def normalize_conversion_type(value: Any) -> str:
    raw = _clean(value, limit=64).lower().replace("-", "_").replace(" ", "_")
    normalized = _CONVERSION_ALIASES.get(raw, raw)
    if normalized not in _ALLOWED_CONVERSION_TYPES:
        raise ValueError(f"unsupported_conversion_type:{normalized or 'empty'}")
    return normalized


def normalize_attribution(value: dict[str, Any] | None) -> dict[str, str]:
    source = dict(value or {})
    out: dict[str, str] = {}
    for key in _ATTRIBUTION_KEYS:
        cleaned = _clean(source.get(key), limit=240 if key == "payload" else 120)
        if cleaned:
            out[key] = cleaned
    if "source" not in out and "utm_source" in out:
        out["source"] = out["utm_source"]
    if "campaign" not in out and "utm_campaign" in out:
        out["campaign"] = out["utm_campaign"]
    if "creative" not in out and "utm_creative" in out:
        out["creative"] = out["utm_creative"]
    return out


def normalize_payload(value: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    out: dict[str, Any] = {}
    for key, raw in source.items():
        clean_key = _clean(key, limit=64)
        if not clean_key:
            continue
        if isinstance(raw, bool):
            out[clean_key] = raw
        elif isinstance(raw, int):
            out[clean_key] = raw
        elif isinstance(raw, float):
            out[clean_key] = round(raw, 6)
        elif raw is None:
            continue
        else:
            cleaned = _clean(raw, limit=320)
            if cleaned:
                out[clean_key] = cleaned
        if len(out) >= 24:
            break
    return out


def stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_conversion_idempotency_key(
    *,
    conversion_type: str,
    source_platform: Any,
    source_event: Any,
    external_event_id: Any,
    user_id: Any,
    amount_minor: Any,
    currency: Any,
    payload: dict[str, Any] | None = None,
) -> str:
    normalized_type = normalize_conversion_type(conversion_type)
    platform = _clean(source_platform, limit=64).lower() or "unknown"
    event_id = _clean(external_event_id, limit=192)

    # A provider/event ID is the stable business identity. Webhook retries may
    # legitimately carry corrected amount/metadata; those must update evidence,
    # not create duplicate conversion records. Fields below are fallback identity
    # only for sources that do not provide an external event ID.
    if event_id:
        seed = {
            "conversion_type": normalized_type,
            "source_platform": platform,
            "external_event_id": event_id,
        }
    else:
        seed = {
            "conversion_type": normalized_type,
            "source_platform": platform,
            "source_event": _clean(source_event, limit=96).lower(),
            "user_id": safe_int(user_id),
            "amount_minor": max(0, safe_int(amount_minor)),
            "currency": (_clean(currency, limit=12) or "RUB").upper(),
            "payload": normalize_payload(payload),
        }
    digest = hashlib.sha256(stable_json(seed).encode("utf-8")).hexdigest()[:32]
    return f"growth_conversion:v1:{normalized_type}:{digest}"


def build_dry_run_conversion(
    *,
    conversion_type: str,
    source_platform: Any,
    source_event: Any,
    external_event_id: Any,
    user_id: Any = 0,
    amount_minor: Any = 0,
    currency: Any = "RUB",
    attribution: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    target_provider: Any = "none",
) -> dict[str, Any]:
    normalized_type = normalize_conversion_type(conversion_type)
    normalized_payload = normalize_payload(payload)
    item = {
        "conversion_type": normalized_type,
        "source_platform": _clean(source_platform, limit=64).lower() or "unknown",
        "source_event": _clean(source_event, limit=96).lower() or normalized_type,
        "external_event_id": _clean(external_event_id, limit=192),
        "user_id": safe_int(user_id),
        "amount_minor": max(0, safe_int(amount_minor)),
        "currency": (_clean(currency, limit=12) or "RUB").upper(),
        "attribution": normalize_attribution(attribution),
        "payload": normalized_payload,
        "target_provider": _clean(target_provider, limit=64).lower() or "none",
        "mode": DRY_RUN_MODE,
        "status": DRY_RUN_STATUS,
        "dispatch_allowed": DISPATCH_ALLOWED,
    }
    item["idempotency_key"] = build_conversion_idempotency_key(
        conversion_type=normalized_type,
        source_platform=item["source_platform"],
        source_event=item["source_event"],
        external_event_id=item["external_event_id"],
        user_id=item["user_id"],
        amount_minor=item["amount_minor"],
        currency=item["currency"],
        payload=normalized_payload,
    )
    return item


def payment_conversion_type(*, gift: bool = False) -> str:
    return "gift_paid" if bool(gift) else "payment_success"


def format_conversion_hub_report(snapshot: dict[str, Any]) -> str:
    counts = dict(snapshot.get("counts") or {})
    latest = list(snapshot.get("latest") or [])
    lines = [
        "🧪 Conversion Hub",
        "dry-run outbox: фиксирует конверсии, но ничего не отправляет наружу",
        "",
        f"Период: {snapshot.get('period')}",
        f"Всего planned: {safe_int(snapshot.get('total'))}",
        f"Payment success: {safe_int(counts.get('payment_success'))}",
        f"Gift paid: {safe_int(counts.get('gift_paid'))}",
        f"Demo ack: {safe_int(counts.get('demo_ack'))}",
        f"Tariff open: {safe_int(counts.get('tariff_open'))}",
        "",
        "Safety lock:",
        f"— mode={DRY_RUN_MODE}",
        f"— dispatch_allowed={DISPATCH_ALLOWED}",
        "— sender/flush наружу отсутствует;",
        "— ошибки Growth-контура не влияют на оплату и выдачу доступа.",
    ]
    if latest:
        lines += ["", "Последние конверсии:"]
        for item in latest[:8]:
            lines.append(
                f"#{safe_int(item.get('id'))} {item.get('conversion_type')} / {item.get('source_platform')} "
                f"/ user={safe_int(item.get('user_id'))} / amount={safe_int(item.get('amount_minor'))} {item.get('currency')}"
            )
    return "\n".join(lines)
