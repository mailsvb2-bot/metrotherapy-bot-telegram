from __future__ import annotations

import base64
import json
import logging
import os
import urllib.error
import urllib.request
import uuid

log = logging.getLogger(__name__)


class YooKassaCheckoutError(RuntimeError):
    """Raised when YooKassa checkout creation fails."""


def _env_value(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def build_yookassa_receipt(*, amount_value: str, description: str) -> dict:
    """Build fiscal receipt payload for YooKassa.

    This belongs to the payments provider layer, not webhook runtime. Runtime
    code may expose HTTP ingress, but provider-specific side effects must stay
    behind the payments service boundary.
    """
    customer_email = (
        os.environ.get("YOOKASSA_RECEIPT_EMAIL")
        or os.environ.get("PAYMENT_RECEIPT_EMAIL")
        or os.environ.get("ADMIN_EMAIL")
        or "support@metrotherapy.ru"
    ).strip()

    vat_code = int((os.environ.get("YOOKASSA_VAT_CODE") or "1").strip())
    payment_mode = (os.environ.get("YOOKASSA_PAYMENT_MODE") or "full_prepayment").strip()
    payment_subject = (os.environ.get("YOOKASSA_PAYMENT_SUBJECT") or "service").strip()

    return {
        "customer": {"email": customer_email},
        "items": [
            {
                "description": (description or "Метротерапия")[:128],
                "quantity": "1.00",
                "amount": {"value": amount_value, "currency": "RUB"},
                "vat_code": vat_code,
                "payment_mode": payment_mode,
                "payment_subject": payment_subject,
            }
        ],
    }


def create_yookassa_confirmation_url(*, source: str, external_user_id: str, kind: str = "subscription") -> str:
    """Create a YooKassa payment and return the redirect confirmation URL.

    Network I/O is intentionally centralized here so runtime ingress modules do
    not own raw provider HTTP calls.
    """
    shop_id = _env_value("YOOKASSA_SHOP_ID")
    secret_key = _env_value("YOOKASSA_SECRET_KEY")

    if not shop_id:
        raise YooKassaCheckoutError("YOOKASSA_SHOP_ID is empty")
    if not secret_key:
        raise YooKassaCheckoutError("YOOKASSA_SECRET_KEY is empty")

    kind = (kind or "subscription").strip().lower()
    is_gift = kind == "gift"

    amount_raw = (
        os.environ.get("GIFT_PAYMENT_AMOUNT_RUB") if is_gift else os.environ.get("PAYMENT_AMOUNT_RUB")
    ) or os.environ.get("PAYMENT_AMOUNT_RUB") or "990"

    try:
        amount_value = f"{float(str(amount_raw).replace(',', '.')):.2f}"
    except ValueError as exc:
        raise YooKassaCheckoutError(f"Invalid payment amount: {amount_raw!r}") from exc

    description = (
        os.environ.get("GIFT_PAYMENT_DESCRIPTION") if is_gift else os.environ.get("PAYMENT_DESCRIPTION")
    ) or ("Метротерапия — подарок" if is_gift else "Метротерапия — доступ к аудиопрактикам")

    return_url = (
        os.environ.get("PAYMENT_RETURN_URL")
        or os.environ.get("SITE_PUBLIC_URL")
        or "https://metrotherapy.ru"
    ).strip()

    payload = {
        "amount": {"value": amount_value, "currency": "RUB"},
        "capture": True,
        "description": description[:128],
        "confirmation": {"type": "redirect", "return_url": return_url},
        "metadata": {
            "project": "metrotherapy",
            "source": str(source or "unknown"),
            "external_user_id": str(external_user_id or ""),
            "kind": kind,
        },
        "receipt": build_yookassa_receipt(amount_value=amount_value, description=description),
    }

    encoded_auth = base64.b64encode(f"{shop_id}:{secret_key}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        "https://api.yookassa.ru/v3/payments",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Basic {encoded_auth}",
            "Content-Type": "application/json",
            "Idempotence-Key": str(uuid.uuid4()),
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        log.error("YooKassa payment creation failed: status=%s body=%s", exc.code, body)
        raise YooKassaCheckoutError(f"YooKassa HTTP {exc.code}") from exc
    except OSError as exc:
        raise YooKassaCheckoutError(f"YooKassa network error: {exc}") from exc

    data = json.loads(raw or "{}")
    confirmation = data.get("confirmation") or {}
    confirmation_url = confirmation.get("confirmation_url") or confirmation.get("url")
    if not confirmation_url:
        log.error("YooKassa payment response without confirmation_url: %s", data)
        raise YooKassaCheckoutError("YooKassa response without confirmation_url")

    log.info(
        "YooKassa payment created: source=%s external_user_id=%s kind=%s amount=%s",
        source,
        external_user_id,
        kind,
        amount_value,
    )
    return str(confirmation_url)
