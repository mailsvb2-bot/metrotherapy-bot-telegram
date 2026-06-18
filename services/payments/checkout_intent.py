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
    raw = os.getenv("PAYMENT_CHECKOUT_INTENT_REQUIRED")
    if raw is not None:
        return _truthy(raw)
    if _is_prod() and _truthy(os.getenv("ALLOW_UNSIGNED_PAYMENT_CHECKOUT_IN_PROD")):
        return False
    return _is_prod()


def checkout_intent_key() -> str:
    return (os.getenv("PAYMENT_CHECKOUT_SIGNING_KEY") or os.getenv("CHECKOUT_SIGNING_KEY") or "").strip()


def _key_for_signing() -> str:
    key = checkout_intent_key()
    if not key:
        raise CheckoutIntentError("PAYMENT_CHECKOUT_SIGNING_KEY is required")
    return key


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
    ttl_sec: int | None = None,
) -> str:
    now = int(time.time())
    ttl = int(ttl_sec or int(os.getenv("PAYMENT_CHECKOUT_INTENT_TTL_SEC", "900") or "900"))
    ttl = max(60, min(ttl, 24 * 60 * 60))
    payload: dict[str, Any] = {
        "v": 1,
        "sub": str(user_id),
        "package_id": str(package_id or ""),
        "kind": _canonical_kind(kind, package_id),
        "source": str(source or "unknown")[:32],
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
    for key, expected in checks.items():
        if str(payload.get(key) or "") != expected:
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
) -> str:
    parts = urlsplit(str(url))
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    params["intent"] = sign_checkout_intent(
        user_id=user_id,
        package_id=package_id,
        kind=kind,
        source=source,
        gift_token=gift_token,
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))
