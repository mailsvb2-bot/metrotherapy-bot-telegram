from __future__ import annotations

from typing import Any

from services.acquisition_attribution import start_attribution_meta
from services.admin_ad_links import build_start_url
from services.events import log_runtime_event

_MAX_PAYLOAD_LEN = 512
_MAX_FIELD_LEN = 160


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


def record_click_redirect(payload: Any, *, request_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Record an ad redirect click as best-effort analytics.

    This function must never block the user's route to Telegram. The underlying
    event logger is already best-effort; input cleanup here keeps the event small
    and intentionally avoids storing IP addresses.
    """

    cleaned_payload = clean_click_payload(payload)
    meta = start_attribution_meta(cleaned_payload)
    meta["payload"] = cleaned_payload
    meta["click_event"] = "ad_click_redirect"
    meta["redirect_target"] = "telegram_start"
    for key, value in (request_meta or {}).items():
        if key in {"user_agent", "referer"}:
            cleaned = _clean_meta_value(value)
            if cleaned:
                meta[key] = cleaned
    log_runtime_event(0, event_type="ad_click_redirect", payload=meta, source="growth_redirect")
    return meta
