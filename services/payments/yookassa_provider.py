from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

log = logging.getLogger(__name__)

_GRANT_KINDS = {"tokens", "practices", "practice_package"}


class YooKassaProviderVerificationError(RuntimeError):
    """Raised when a YooKassa webhook cannot be verified as a provider fact."""


def _is_prod() -> bool:
    return (os.getenv("APP_ENV", "dev") or "dev").strip().lower() in {"prod", "production"}


def _truthy(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def provider_verification_required() -> bool:
    """Require provider source-of-truth verification unconditionally in production.

    Development and hermetic tests may opt in explicitly. Production must not have
    an environment-variable bypass because an unverified successful payload can
    otherwise reach the payment grant path.
    """

    if _is_prod():
        return True
    raw = os.getenv("YOOKASSA_PROVIDER_VERIFICATION_REQUIRED")
    return _truthy(raw) if raw is not None else False


def _auth_header() -> str:
    shop_id = (os.getenv("YOOKASSA_SHOP_ID") or "").strip()
    api_key = (os.getenv("YOOKASSA_SECRET_KEY") or "").strip()
    if not shop_id or not api_key:
        raise YooKassaProviderVerificationError("missing_yookassa_credentials")
    encoded = base64.b64encode(f"{shop_id}:{api_key}".encode("utf-8")).decode("ascii")
    return f"Basic {encoded}"


def fetch_yookassa_payment(payment_id: str) -> dict[str, Any]:
    payment_id = (payment_id or "").strip()
    if not payment_id:
        raise YooKassaProviderVerificationError("missing_provider_payment_id")
    req = urllib.request.Request(
        f"https://api.yookassa.ru/v3/payments/{payment_id}",
        method="GET",
        headers={"Authorization": _auth_header(), "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        log.error("YooKassa payment verification failed: status=%s body=%s", exc.code, "<redacted>" if _is_prod() else body[:1000])
        raise YooKassaProviderVerificationError(f"provider_http_{exc.code}") from exc
    except OSError as exc:
        raise YooKassaProviderVerificationError(f"provider_network:{type(exc).__name__}") from exc
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise YooKassaProviderVerificationError("provider_bad_json") from exc
    if not isinstance(payload, dict):
        raise YooKassaProviderVerificationError("provider_bad_payload")
    return payload


def _minor(amount: dict[str, Any] | None) -> int:
    if not isinstance(amount, dict):
        return 0
    raw = str(amount.get("value") or "0").replace(",", ".").strip()
    try:
        value = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return 0
    return int(value * 100)


def _object(payload: dict[str, Any]) -> dict[str, Any]:
    obj = payload.get("object")
    return obj if isinstance(obj, dict) else {}


def _amount(payload: dict[str, Any]) -> dict[str, Any]:
    amount = payload.get("amount")
    return amount if isinstance(amount, dict) else {}


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    meta = payload.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _grant_candidate(payload: dict[str, Any]) -> bool:
    obj = _object(payload)
    status = str(obj.get("status") or payload.get("status") or "").strip().lower()
    event = str(payload.get("event") or "").strip().lower()
    meta = _metadata(obj)
    kind = str(meta.get("kind") or "").strip().lower()
    package_id = str(meta.get("package_id") or "").strip()
    return (event == "payment.succeeded" or status == "succeeded") and (kind in _GRANT_KINDS or bool(package_id))


def webhook_requires_provider_verification(payload: dict[str, Any]) -> bool:
    """Every persisted payment webhook must be authenticated in production.

    Previously only grant-producing success events were checked against YooKassa.
    Anyone could therefore submit a forged pending/canceled/non-package event and
    pollute payment status, support reports and conversion analytics. A payment id
    is enough to query the provider source of truth, so all payment facts use the
    same verification boundary.
    """

    obj = _object(payload)
    return bool(str(obj.get("id") or payload.get("id") or "").strip())


def verify_yookassa_webhook_with_provider(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Verify a webhook and return the canonical provider payment object.

    The caller must persist the returned object rather than the untrusted webhook
    object. This also handles stale webhook delivery safely: current provider
    status wins instead of accepting a forged or outdated event/status pair.
    """

    if not provider_verification_required():
        return None
    if not webhook_requires_provider_verification(payload):
        raise YooKassaProviderVerificationError("missing_provider_payment_id")

    obj = _object(payload)
    payment_id = str(obj.get("id") or payload.get("id") or "").strip()
    provider = fetch_yookassa_payment(payment_id)

    if str(provider.get("id") or "").strip() != payment_id:
        raise YooKassaProviderVerificationError("provider_id_mismatch")
    if _grant_candidate(payload) and str(provider.get("status") or "").strip().lower() != "succeeded":
        raise YooKassaProviderVerificationError("provider_status_not_succeeded")

    webhook_amount = _amount(obj)
    provider_amount = _amount(provider)
    if _minor(webhook_amount) != _minor(provider_amount):
        raise YooKassaProviderVerificationError("provider_amount_mismatch")
    if str(webhook_amount.get("currency") or "RUB").upper() != str(provider_amount.get("currency") or "RUB").upper():
        raise YooKassaProviderVerificationError("provider_currency_mismatch")

    webhook_meta = _metadata(obj)
    provider_meta = _metadata(provider)
    for key in ("external_user_id", "user_id", "kind", "package_id", "gift_token"):
        left = str(webhook_meta.get(key) or "").strip()
        right = str(provider_meta.get(key) or "").strip()
        if left != right:
            raise YooKassaProviderVerificationError(f"provider_metadata_{key}_mismatch")
    return provider
