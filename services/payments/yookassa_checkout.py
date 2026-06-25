from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
import uuid

from services.gift_claims import is_gift_token, normalize_gift_token
from services.practice_token_contract import package_by_id

log = logging.getLogger(__name__)


class YooKassaCheckoutError(RuntimeError):
    """Raised when YooKassa checkout creation fails."""


def _env_value(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _is_prod() -> bool:
    return (_env_value("APP_ENV", "dev") or "dev").lower() in {"prod", "production"}


def _truthy_env(name: str, default: str = "0") -> bool:
    return (_env_value(name, default) or default).lower() in {"1", "true", "yes", "on"}


def _provider_error_body_for_log(body: str) -> str:
    """Keep provider diagnostics useful without leaking sensitive payloads in prod logs."""
    if _is_prod():
        return "<redacted in prod>"
    return (body or "").replace("\n", " ")[:1000]


def _explicit_idempotence_key_allowed() -> bool:
    """Allow static YooKassa idempotence keys only for intentional non-prod probes.

    A process-wide PAYMENT_IDEMPOTENCE_KEY/YOOKASSA_IDEMPOTENCE_KEY is useful for
    local/manual replay drills, but it is dangerous in production: unrelated
    checkout attempts can collapse into one provider-side idempotent operation.
    Production requests must use the per-intent key derived below.
    """
    if not _is_prod():
        return True
    return _truthy_env("ALLOW_STATIC_PAYMENT_IDEMPOTENCE_KEY_IN_PROD")


def _checkout_intent_id(checkout_intent: str | None) -> str | None:
    """Return a stable provider-safe id derived from one signed checkout intent.

    The public checkout URL can be retried by browsers, messengers, previews or
    users. A retry of the exact same signed intent must map to the exact same
    YooKassa Idempotence-Key, otherwise one URL can create multiple provider
    payments. We hash only the signed body component: it is stable for the intent
    and avoids storing the signature itself in provider metadata.
    """
    token = (checkout_intent or "").strip()
    if not token:
        return None
    body = token.split(".", 1)[0].strip()
    if not body:
        return None
    digest = hashlib.sha256(body.encode("ascii", errors="ignore")).hexdigest()[:40]
    return f"ci_{digest}"


def _idempotence_key(*, source: str, external_user_id: str, kind: str, amount_value: str, intent_id: str | None = None) -> str:
    explicit = _env_value("PAYMENT_IDEMPOTENCE_KEY") or _env_value("YOOKASSA_IDEMPOTENCE_KEY")
    if explicit:
        if not _explicit_idempotence_key_allowed():
            raise YooKassaCheckoutError(
                "Static YooKassa idempotence key is forbidden in prod. "
                "Unset PAYMENT_IDEMPOTENCE_KEY/YOOKASSA_IDEMPOTENCE_KEY or use an explicit guarded drill flag."
            )
        return explicit[:128]
    if intent_id:
        return f"metrotherapy:{intent_id}"[:128]
    return str(uuid.uuid4())


def _receipt_customer_email() -> str:
    explicit = (
        os.environ.get("YOOKASSA_RECEIPT_EMAIL")
        or os.environ.get("PAYMENT_RECEIPT_EMAIL")
        or os.environ.get("ADMIN_EMAIL")
        or ""
    ).strip()
    if explicit:
        return explicit
    if _is_prod():
        raise YooKassaCheckoutError(
            "YOOKASSA_RECEIPT_EMAIL or PAYMENT_RECEIPT_EMAIL or ADMIN_EMAIL is required in prod"
        )
    return "support@metrotherapy.ru"


def build_yookassa_receipt(*, amount_value: str, description: str) -> dict:
    customer_email = _receipt_customer_email()
    vat_code = int((os.environ.get("YOOKASSA_VAT_CODE") or "1").strip())
    payment_mode = (os.environ.get("YOOKASSA_PAYMENT_MODE") or "full_prepayment").strip()
    payment_subject = (os.environ.get("YOOKASSA_PAYMENT_SUBJECT") or "service").strip()
    return {
        "customer": {"email": customer_email},
        "items": [
            {
                "description": (description or "Metrotherapy")[:128],
                "quantity": "1.00",
                "amount": {"value": amount_value, "currency": "RUB"},
                "vat_code": vat_code,
                "payment_mode": payment_mode,
                "payment_subject": payment_subject,
            }
        ],
    }


def _legacy_amount_description(kind: str) -> tuple[str, str]:
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
    ) or ("Metrotherapy gift" if is_gift else "Metrotherapy access")
    return amount_value, description


def create_yookassa_confirmation_url(
    *,
    source: str,
    external_user_id: str,
    kind: str = "subscription",
    package_id: str | None = None,
    gift_token: str | None = None,
    checkout_intent: str | None = None,
) -> str:
    """Create a YooKassa payment and return the redirect confirmation URL."""
    shop_id = _env_value("YOOKASSA_SHOP_ID")
    secret_key = _env_value("YOOKASSA_SECRET_KEY")
    if not shop_id:
        raise YooKassaCheckoutError("YOOKASSA_SHOP_ID is empty")
    if not secret_key:
        raise YooKassaCheckoutError("YOOKASSA_SECRET_KEY is empty")

    kind = (kind or "subscription").strip().lower()
    intent_id = _checkout_intent_id(checkout_intent) or f"pi_{uuid.uuid4().hex}"
    package = None
    normalized_gift_token = normalize_gift_token(gift_token)
    if normalized_gift_token and not is_gift_token(normalized_gift_token):
        raise YooKassaCheckoutError("Invalid gift token")

    if kind in {"tokens", "practices", "practice_package"}:
        kind = "tokens"
        package = package_by_id(package_id)
        amount_value = f"{float(package.price_rub):.2f}"
        description = f"Metrotherapy - {package.title}"
    else:
        amount_value, description = _legacy_amount_description(kind)

    return_url = (
        os.environ.get("PAYMENT_RETURN_URL")
        or os.environ.get("SITE_PUBLIC_URL")
        or "https://metrotherapy.ru"
    ).strip()

    metadata = {
        "project": "metrotherapy",
        "source": str(source or "unknown"),
        "external_user_id": str(external_user_id or ""),
        "user_id": str(external_user_id or ""),
        "kind": kind,
        "intent_id": intent_id,
    }
    if package is not None:
        metadata.update({"package_id": package.package_id, "tokens": str(package.tokens)})
    if normalized_gift_token:
        metadata.update({"gift_token": normalized_gift_token, "gift": "1"})

    payload = {
        "amount": {"value": amount_value, "currency": "RUB"},
        "capture": True,
        "description": description[:128],
        "confirmation": {"type": "redirect", "return_url": return_url},
        "metadata": metadata,
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
            "Idempotence-Key": _idempotence_key(
                source=source,
                external_user_id=external_user_id,
                kind=kind,
                amount_value=amount_value,
                intent_id=intent_id,
            ),
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        log.error("YooKassa payment creation failed: status=%s body=%s", exc.code, _provider_error_body_for_log(body))
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
        "YooKassa payment created: source=%s external_user_id=%s kind=%s amount=%s package_id=%s gift=%s",
        source,
        external_user_id,
        kind,
        amount_value,
        package.package_id if package else None,
        bool(normalized_gift_token),
    )
    return str(confirmation_url)
