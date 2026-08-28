from __future__ import annotations

from html import escape
from typing import Any
from urllib.parse import quote

from services.acquisition_attribution import start_attribution_meta
from services.admin_ad_links import build_start_url
from services.events import log_runtime_event
from services.messenger.links import build_entry_targets

_MAX_PAYLOAD_LEN = 512
_MAX_FIELD_LEN = 160
_PLATFORM_ORDER = {"telegram": 0, "vk": 1, "max": 2}


def clean_click_payload(payload: Any) -> str:
    text = str(payload or "").strip()
    text = text.replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    return text[:_MAX_PAYLOAD_LEN]


def _clean_meta_value(value: Any, *, limit: int = _MAX_FIELD_LEN) -> str:
    text = str(value or "").strip().replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    return text[:limit]


def build_click_redirect_target(payload: Any) -> str:
    return build_start_url(clean_click_payload(payload))


def build_choice_targets(payload: Any) -> list[dict[str, str]]:
    cleaned_payload = clean_click_payload(payload)
    targets = build_entry_targets(cleaned_payload)
    return sorted(targets, key=lambda item: _PLATFORM_ORDER.get(item.get("platform", ""), 99))


def build_platform_redirect_target(payload: Any, platform: str) -> str:
    wanted = str(platform or "").strip().lower()
    for item in build_choice_targets(payload):
        if item.get("platform") == wanted:
            return str(item.get("url") or "")
    return ""


def build_choice_path(payload: Any, platform: str) -> str:
    cleaned_payload = clean_click_payload(payload)
    cleaned_platform = str(platform or "").strip().lower()
    return f"/pay/r/{quote(cleaned_payload, safe='')}/{quote(cleaned_platform, safe='')}"


def render_messenger_choice_html(payload: Any) -> str:
    targets = build_choice_targets(payload)
    buttons = "".join(
        (
            '<a class="messenger" href="'
            + escape(build_choice_path(payload, item["platform"]), quote=True)
            + '">'
            + escape(item["title"])
            + "</a>"
        )
        for item in targets
    )
    return (
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Метротерапия — выберите мессенджер</title>"
        "<style>body{font-family:system-ui,-apple-system,sans-serif;background:#f6f7f9;color:#18212f;"
        "margin:0;padding:32px 18px}.card{max-width:520px;margin:8vh auto;background:#fff;border-radius:20px;"
        "padding:28px;box-shadow:0 12px 36px #00000012}h1{font-size:26px;margin:0 0 12px}"
        "p{line-height:1.5;color:#52606d}.messenger{display:block;text-decoration:none;text-align:center;"
        "padding:14px 18px;margin:12px 0;border-radius:12px;background:#172b4d;color:#fff;font-weight:700}"
        "small{display:block;margin-top:18px;color:#7b8794;line-height:1.4}</style></head><body><main class=\"card\">"
        "<h1>Где вам удобнее проходить аудиопрактики?</h1>"
        "<p>Выберите привычный мессенджер. Устанавливать отдельное приложение не нужно.</p>"
        + buttons
        + "<small>Метротерапия — цифровые аудиопрактики для повседневной саморегуляции. "
        "Сервис не заменяет медицинскую или психологическую помощь.</small></main></body></html>"
    )


def record_click_redirect(
    payload: Any,
    *,
    request_meta: dict[str, Any] | None = None,
    redirect_target: str = "telegram_start",
) -> dict[str, Any]:
    cleaned_payload = clean_click_payload(payload)
    meta = start_attribution_meta(cleaned_payload)
    meta["payload"] = cleaned_payload
    meta["click_event"] = "ad_click_redirect"
    meta["redirect_target"] = _clean_meta_value(redirect_target, limit=64) or "telegram_start"
    for key, value in (request_meta or {}).items():
        if key in {"user_agent", "referer"}:
            cleaned = _clean_meta_value(value)
            if cleaned:
                meta[key] = cleaned
    log_runtime_event(0, event_type="ad_click_redirect", payload=meta, source="growth_redirect")
    return meta


def record_choice_landing(payload: Any, *, request_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    return record_click_redirect(
        payload, request_meta=request_meta, redirect_target="messenger_choice"
    )


def record_messenger_choice(
    payload: Any,
    *,
    platform: str,
    request_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cleaned_payload = clean_click_payload(payload)
    cleaned_platform = str(platform or "").strip().lower()[:32]
    meta = start_attribution_meta(cleaned_payload)
    meta["payload"] = cleaned_payload
    meta["messenger"] = cleaned_platform
    meta["choice_event"] = "messenger_choice"
    for key, value in (request_meta or {}).items():
        if key in {"user_agent", "referer"}:
            cleaned = _clean_meta_value(value)
            if cleaned:
                meta[key] = cleaned
    log_runtime_event(0, event_type="messenger_choice", payload=meta, source="growth_redirect")
    return meta
