from __future__ import annotations

import asyncio
import logging
import os

from aiohttp import web

from services.payments.reconciliation import record_yookassa_webhook
from services.payments.yookassa_checkout import create_yookassa_confirmation_url

log = logging.getLogger(__name__)


def _create_yookassa_payment(*, source: str, external_user_id: str, kind: str = "subscription", **_: object) -> str:
    return create_yookassa_confirmation_url(
        source=source,
        external_user_id=external_user_id,
        kind=kind,
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


async def pay_yookassa_web(request: web.Request) -> web.Response:
    source = (request.query.get("source") or "unknown").strip()[:32]
    external_user_id = (request.query.get("user_id") or "").strip()[:64]
    kind = (request.query.get("kind") or "subscription").strip().casefold()
    if kind not in {"subscription", "gift"}:
        kind = "subscription"

    try:
        confirmation_url = await asyncio.to_thread(
            _create_yookassa_payment,
            source=source,
            external_user_id=external_user_id,
            kind=kind,
        )
    except Exception as exc:  # validator: allow-wide-except
        log.exception("YooKassa web payment endpoint failed")
        return web.Response(
            status=500,
            text=(
                "Не удалось создать платёж YooKassa. "
                "Проверьте YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY и доступ сервера к api.yookassa.ru. "
                f"Ошибка: {type(exc).__name__}"
            ),
            content_type="text/plain",
        )

    raise web.HTTPFound(location=confirmation_url)


async def yookassa_reconciliation_webhook(request: web.Request) -> web.Response:
    """Provider reconciliation endpoint.

    This is not a Telegram webhook and does not change Telegram polling mode.
    It only records external YooKassa payment facts for support/reconciliation.
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
