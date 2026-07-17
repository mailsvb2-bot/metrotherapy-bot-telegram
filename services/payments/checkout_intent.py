from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class CheckoutIntentError(ValueError):
    """Raised when a public checkout URL has a missing or invalid signed intent."""


def _is_prod() -> bool:
    return (os.getenv("APP_ENV", "dev") or "dev").strip().lower() in {"prod", "production"}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def checkout_intent_required() -> bool:
    """Require signed checkout intents unconditionally in production."""

    if _is_prod():
        return True
    raw = os.getenv("PAYMENT_CHECKOUT_INTENT_REQUIRED")
    return _truthy(raw) if raw is not None else False


def checkout_intent_key() -> str:
    return (os.getenv("PAYMENT_CHECKOUT_SIGNING_KEY") or os.getenv("CHECKOUT_SIGNING_KEY") or "").strip()


def _key_for_signing() -> str:
    key = checkout_intent_key()
    if key:
        return key
    if _is_prod():
        raise CheckoutIntentError("PAYMENT_CHECKOUT_SIGNING_KEY is required in prod")
    return "-".join(("metrotherapy", "dev", "checkout", "key"))


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + pad).encode("ascii"))


def _canonical_kind(kind: str | None, package_id: str | None = None) -> str:
    normalized = (kind or "tokens").strip().lower()
    if (package_id or "").strip():
        return "tokens"
    if normalized in {"tokens", "practices", "practice_package"}:
        return "tokens"
    if normalized in {"subscription", "gift"}:
        return normalized
    return "tokens"


def _canonical_source(source: str | None) -> str:
    normalized = str(source or "unknown").strip().casefold()
    aliases = {"vkontakte": "vk", "вконтакте": "vk", "website": "web", "site": "web"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"telegram", "vk", "max", "web"}:
        return "unknown"
    return normalized


def _canonical_currency(currency: str | None) -> str:
    value = str(currency or "RUB").strip().upper()
    if not value or len(value) > 12 or not value.isascii() or not value.isalnum():
        raise CheckoutIntentError("invalid_checkout_currency")
    return value


def _package_amount(package_id: str | None) -> tuple[int, str]:
    package = str(package_id or "").strip()
    if not package:
        return 0, "RUB"
    try:
        from services.practice_token_contract import package_by_id

        return int(package_by_id(package).price_rub) * 100, "RUB"
    except (ImportError, ValueError):
        return 0, "RUB"


def _canonical_amount(
    package_id: str | None,
    amount_minor: int | str | None,
    currency: str | None,
) -> tuple[int, str]:
    derived_amount, derived_currency = _package_amount(package_id)
    amount = derived_amount if amount_minor is None else int(amount_minor)
    if amount < 0:
        raise CheckoutIntentError("invalid_checkout_amount")
    return amount, _canonical_currency(currency or derived_currency)


def _signature(signing_input: str) -> str:
    digest = hmac.new(_key_for_signing().encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return _b64e(digest)


def sign_checkout_intent(
    *,
    user_id: int | str,
    package_id: str,
    kind: str = "tokens",
    source: str = "telegram",
    gift_token: str | None = None,
    amount_minor: int | str | None = None,
    currency: str | None = None,
    ttl_sec: int | None = None,
) -> str:
    now = int(time.time())
    ttl = int(ttl_sec or int(os.getenv("PAYMENT_CHECKOUT_INTENT_TTL_SEC", "900") or "900"))
    ttl = max(60, min(ttl, 24 * 60 * 60))
    canonical_amount, canonical_currency = _canonical_amount(package_id, amount_minor, currency)
    payload: dict[str, Any] = {
        "v": 2,
        "sub": str(user_id),
        "package_id": str(package_id or ""),
        "kind": _canonical_kind(kind, package_id),
        "source": _canonical_source(source),
        "amount_minor": canonical_amount,
        "currency": canonical_currency,
        "gift_token": str(gift_token or ""),
        "iat": now,
        "exp": now + ttl,
        "nonce": uuid.uuid4().hex,
    }
    body = _b64e(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{body}.{_signature(body)}"


def verify_checkout_intent(
    token: str,
    *,
    expected_user_id: int | str,
    expected_package_id: str,
    expected_kind: str = "tokens",
    expected_source: str | None = None,
    expected_amount_minor: int | str | None = None,
    expected_currency: str | None = None,
    expected_gift_token: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    token = (token or "").strip()
    if not token:
        raise CheckoutIntentError("missing_checkout_intent")
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise CheckoutIntentError("malformed_checkout_intent") from exc
    expected_sig = _signature(body)
    if not hmac.compare_digest(sig, expected_sig):
        raise CheckoutIntentError("bad_checkout_intent_signature")
    try:
        payload = json.loads(_b64d(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CheckoutIntentError("bad_checkout_intent_payload") from exc
    if not isinstance(payload, dict):
        raise CheckoutIntentError("bad_checkout_intent_payload")
    if int(payload.get("v") or 0) != 2:
        raise CheckoutIntentError("unsupported_checkout_intent_version")

    ts = int(now if now is not None else time.time())
    exp = int(payload.get("exp") or 0)
    iat = int(payload.get("iat") or 0)
    if exp <= ts:
        raise CheckoutIntentError("expired_checkout_intent")
    if iat > ts + 60:
        raise CheckoutIntentError("future_checkout_intent")

    checks = {
        "sub": str(expected_user_id),
        "package_id": str(expected_package_id or ""),
        "kind": _canonical_kind(expected_kind, expected_package_id),
        "gift_token": str(expected_gift_token or ""),
    }
    if expected_source is not None:
        checks["source"] = _canonical_source(expected_source)
    expected_amount, expected_currency_value = _canonical_amount(
        expected_package_id,
        expected_amount_minor,
        expected_currency,
    )
    if expected_amount_minor is not None or expected_amount > 0:
        checks["amount_minor"] = str(expected_amount)
        checks["currency"] = expected_currency_value

    for key, expected in checks.items():
        if str(payload.get(key) if payload.get(key) is not None else "") != str(expected):
            raise CheckoutIntentError(f"checkout_intent_{key}_mismatch")
    return payload


def add_checkout_intent_to_url(
    url: str,
    *,
    user_id: int | str,
    package_id: str,
    kind: str = "tokens",
    source: str = "telegram",
    gift_token: str | None = None,
    amount_minor: int | str | None = None,
    currency: str | None = None,
) -> str:
    parts = urlsplit(str(url))
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    canonical_amount, canonical_currency = _canonical_amount(package_id, amount_minor, currency)
    params["intent"] = sign_checkout_intent(
        user_id=user_id,
        package_id=package_id,
        kind=kind,
        source=source,
        gift_token=gift_token,
        amount_minor=canonical_amount,
        currency=canonical_currency,
    )
    if canonical_amount > 0:
        params["amount_minor"] = str(canonical_amount)
        params["currency"] = canonical_currency
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))
