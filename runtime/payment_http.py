from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web

from services.payments.reconciliation import record_yookassa_webhook
from services.payments.yookassa_checkout import create_yookassa_confirmation_url
from services.practice_token_contract import package_by_id

log = logging.getLogger(__name__)

_ALLOWED_PAYMENT_KINDS = {"subscription", "gift", "tokens", "practices", "practice_package"}
_TOKEN_PAYMENT_KINDS = {"tokens", "practices", "practice_package"}


def _normalize_payment_kind(kind: str | None, package_id: str | None = None) -> str:
    normalized = (kind or "subscription").strip().casefold()
    if normalized not in _ALLOWED_PAYMENT_KINDS:
        normalized = "subscription"
    if (package_id or "").strip() and normalized != "gift":
        return "tokens"
    return normalized


def _create_yookassa_payment(
    *,
    source: str,
    external_user_id: str,
    kind: str = "subscription",
    package_id: str | None = None,
    **_: object,
) -> str:
    return create_yookassa_confirmation_url(
        source=source,
        external_user_id=external_user_id,
        kind=kind,
        package_id=package_id,
    )


def _webhook_secret() -> str:
    return (
        os.getenv("YOOKASSA_WEBHOOK_SECRET")
        or os.getenv("PAYMENT_WEBHOOK_SECRET")
        or os.getenv("WEBHOOK_SECRET")
        or ""
    ).strip()


def _provided_secret(request: web.Request) -> str:
    return (
        request.headers.get("X-Metrotherapy-Webhook-Secret")
        or request.headers.get("X-Webhook-Secret")
        or request.query.get("secret")
        or ""
    ).strip()


def _webhook_secret_ok(request: web.Request) -> bool:
    expected = _webhook_secret()
    # Prod must be explicit. In dev/test, an empty secret keeps local tests simple.
    prod = (os.getenv("APP_ENV", "dev") or "dev").strip().lower() in {"prod", "production"}
    if not expected:
        return not prod
    return _provided_secret(request) == expected


def _package_error_response(package_id: str) -> web.Response | None:
    if not (package_id or "").strip():
        return web.Response(
            status=400,
            text="Practice package is required.",
            content_type="text/plain",
        )
    try:
        package_by_id(package_id)
    except ValueError:
        return web.Response(
            status=400,
            text="Unknown practice package.",
            content_type="text/plain",
        )
    return None


def _user_id_error_response(user_id: str) -> web.Response | None:
    cleaned = (user_id or "").strip()
    if cleaned.isdigit() and int(cleaned) > 0:
        return None
    return web.Response(
        status=400,
        text="User id is required for practice package checkout.",
        content_type="text/plain",
    )


async def pay_yookassa_web(request: web.Request) -> web.Response:
    source = (request.query.get("source") or "unknown").strip()[:32]
    external_user_id = (request.query.get("user_id") or "").strip()[:64]
    package_id = (request.query.get("package_id") or "").strip()[:64]
    kind = _normalize_payment_kind(request.query.get("kind"), package_id)

    if kind in _TOKEN_PAYMENT_KINDS:
        user_error = _user_id_error_response(external_user_id)
        if user_error is not None:
            return user_error
        package_error = _package_error_response(package_id)
        if package_error is not None:
            return package_error

    try:
        confirmation_url = await asyncio.to_thread(
            _create_yookassa_payment,
            source=source,
            external_user_id=external_user_id,
            kind=kind,
            package_id=package_id or None,
        )
    except Exception as exc:  # validator: allow-wide-except
        log.exception("YooKassa web payment endpoint failed")
        return web.Response(
            status=500,
            text=(
                "Не удалось создать платёж YooKassa. "
                "Проверьте YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY, package_id и доступ сервера к api.yookassa.ru. "
                f"Ошибка: {type(exc).__name__}"
            ),
            content_type="text/plain",
        )

    raise web.HTTPFound(location=confirmation_url)


async def yookassa_reconciliation_webhook(request: web.Request) -> web.Response:
    """Provider reconciliation endpoint.

    This is not a Telegram webhook and does not change Telegram polling mode.
    It records external YooKassa payment facts and, for practice-token payments,
    idempotently grants purchased practices.
    """
    if not _webhook_secret_ok(request):
        return web.json_response({"ok": False, "error": "forbidden"}, status=403)

    try:
        payload = await request.json()
    except Exception as exc:  # validator: allow-wide-except
        return web.json_response({"ok": False, "error": f"bad_json:{type(exc).__name__}"}, status=400)

    if not isinstance(payload, dict):
        return web.json_response({"ok": False, "error": "bad_payload"}, status=400)

    result = await asyncio.to_thread(record_yookassa_webhook, payload)
    status = 200 if result.ok else 400
    return web.json_response(
        {
            "ok": result.ok,
            "provider": result.provider,
            "provider_payment_id": result.provider_payment_id,
            "payment_status": result.status,
            "event": result.event,
            "inserted": result.inserted,
            "problem": result.problem,
        },
        status=status,
    )
