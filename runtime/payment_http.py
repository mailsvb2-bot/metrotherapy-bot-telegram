from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from services.payments.yookassa_checkout import create_yookassa_confirmation_url

log = logging.getLogger(__name__)


def _create_yookassa_payment(*, source: str, external_user_id: str, kind: str = "subscription", **_: object) -> str:
    return create_yookassa_confirmation_url(
        source=source,
        external_user_id=external_user_id,
        kind=kind,
    )


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
