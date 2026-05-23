from __future__ import annotations

import asyncio
import html
import logging
import os
import urllib.parse

from aiohttp import web

from services.payments.reconciliation import record_yookassa_webhook
from services.payments.yookassa_checkout import create_yookassa_confirmation_url
from services.practice_token_contract import package_by_id
from services.practice_tokens import get_active_packages
from services.premium_delivery import flush_premium_delivery_outbox

log = logging.getLogger(__name__)

_ALLOWED_PAYMENT_KINDS = {"subscription", "gift", "tokens", "practices", "practice_package"}
_TOKEN_PAYMENT_KINDS = {"tokens", "practices", "practice_package"}


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


def _normalize_payment_kind(kind: str | None, package_id: str | None = None) -> str:
    """Normalize payment kind before creating a provider checkout.

    Any link that explicitly carries a practice package id is a practice-token
    checkout, even if an older caller still says kind=subscription. This prevents
    mixed links like `kind=subscription&package_id=practice_20` from creating a
    legacy one-off subscription payment and silently ignoring the package id.
    """
    normalized = (kind or "subscription").strip().casefold()
    if normalized not in _ALLOWED_PAYMENT_KINDS:
        normalized = "subscription"
    if (package_id or "").strip() and normalized != "gift":
        return "tokens"
    return normalized


def _package_error_response(package_id: str) -> web.Response | None:
    if not (package_id or "").strip():
        return web.Response(
            status=400,
            text="Не выбран пакет практик. Откройте страницу выбора пакета и выберите 5, 20 или 60 практик.",
            content_type="text/plain",
        )
    try:
        package_by_id(package_id)
    except ValueError:
        return web.Response(
            status=400,
            text=(
                "Неизвестный пакет практик. "
                "Откройте страницу выбора пакета и выберите 5, 20 или 60 практик."
            ),
            content_type="text/plain",
        )
    return None


def _user_id_error_response(user_id: str) -> web.Response | None:
    cleaned = (user_id or "").strip()
    if cleaned.isdigit() and int(cleaned) > 0:
        return None
    return web.Response(
        status=400,
        text="Не удалось определить пользователя для начисления практик. Откройте оплату из бота.",
        content_type="text/plain",
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


def _payment_public_base_url(request: web.Request) -> str:
    configured = (
        os.getenv("PAYMENT_PUBLIC_BASE_URL")
        or os.getenv("MESSENGER_PUBLIC_BASE_URL")
        or ""
    ).strip().rstrip("/")
    if configured:
        return configured
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "https"
    host = request.headers.get("X-Forwarded-Host") or request.host
    return f"{scheme}://{host}".rstrip("/")


def _practice_package_selector_html(request: web.Request, *, source: str, external_user_id: str) -> str:
    base_url = _payment_public_base_url(request)
    escaped_user = html.escape(external_user_id or "")
    links: list[str] = []
    for package in get_active_packages():
        query = urllib.parse.urlencode(
            {
                "source": source or "messenger",
                "user_id": external_user_id or "",
                "kind": "tokens",
                "package_id": package.package_id,
            }
        )
        url = f"{base_url}/pay/yookassa?{query}"
        label = f"{package.title} — {package.price_rub:,} ₽".replace(",", " ")
        links.append(f'<a class="package" href="{html.escape(url)}">{html.escape(label)}</a>')
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Пакеты практик — Метротерапия</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; background: #f6f7fb; color: #111827; }}
    main {{ max-width: 560px; margin: 0 auto; padding: 28px 18px; }}
    .card {{ background: #fff; border-radius: 18px; padding: 22px; box-shadow: 0 12px 28px rgba(15, 23, 42, .08); }}
    h1 {{ font-size: 24px; margin: 0 0 12px; }}
    p {{ line-height: 1.5; color: #4b5563; }}
    .package {{ display: block; margin: 12px 0; padding: 15px 16px; border-radius: 14px; background: #111827; color: #fff; text-decoration: none; font-weight: 700; text-align: center; }}
    .note {{ font-size: 14px; color: #6b7280; margin-top: 16px; }}
  </style>
</head>
<body>
  <main>
    <section class="card">
      <h1>💳 Пакеты практик</h1>
      <p>Выберите пакет. 1 практика = одно аудио с оценкой состояния ДО и ПОСЛЕ. Если аудио не отправилось, практика не списывается.</p>
      {''.join(links)}
      <p class="note">Пользователь: {escaped_user}. Ритм можно выбрать отдельно: только утро, только вечер или утро + вечер.</p>
    </section>
  </main>
</body>
</html>"""


async def pay_yookassa_web(request: web.Request) -> web.Response:
    source = (request.query.get("source") or "unknown").strip()[:32]
    external_user_id = (request.query.get("user_id") or "").strip()[:64]
    package_id = (request.query.get("package_id") or "").strip()[:64]
    kind = _normalize_payment_kind(request.query.get("kind"), package_id)

    if kind == "subscription" and not package_id:
        return web.Response(
            text=_practice_package_selector_html(request, source=source, external_user_id=external_user_id),
            content_type="text/html",
        )

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


async def _flush_premium_delivery_best_effort(request: web.Request) -> None:
    senders = request.app.get("premium_senders")
    if senders is None:
        return
    try:
        result = await flush_premium_delivery_outbox(senders=senders, limit=20)
    except Exception:  # validator: allow-wide-except
        log.exception("Premium delivery outbox flush failed")
        return
    if result.sent or result.failed or result.skipped:
        log.info("Premium delivery flush: sent=%s failed=%s skipped=%s", result.sent, result.failed, result.skipped)


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
    if result.ok and not result.problem:
        await _flush_premium_delivery_best_effort(request)
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