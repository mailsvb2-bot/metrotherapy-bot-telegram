from __future__ import annotations

import json
from urllib.parse import parse_qs, unquote_plus

_ALLOWED_KEYS = {
    "utm_source",
    "source",
    "src",
    "utm_campaign",
    "campaign",
    "camp",
    "utm_creative",
    "creative",
    "utm_content",
    "content",
    "ad_spend",
    "adspend",
    "cost",
}

_KEY_ALIASES = {
    "source": "utm_source",
    "src": "utm_source",
    "campaign": "utm_campaign",
    "camp": "utm_campaign",
    "creative": "utm_creative",
    "content": "utm_content",
    "adspend": "ad_spend",
    "cost": "ad_spend",
}

_TOKEN_PREFIXES = (
    ("utm_source_", "utm_source"),
    ("source_", "utm_source"),
    ("src_", "utm_source"),
    ("utm_campaign_", "utm_campaign"),
    ("campaign_", "utm_campaign"),
    ("camp_", "utm_campaign"),
    ("utm_creative_", "utm_creative"),
    ("creative_", "utm_creative"),
    ("utm_content_", "utm_content"),
    ("content_", "utm_content"),
    ("ad_spend_", "ad_spend"),
    ("adspend_", "ad_spend"),
    ("cost_", "ad_spend"),
)


def _clean(value: object, *, limit: int = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = unquote_plus(text)
    text = " ".join(text.replace("\n", " ").replace("\r", " ").split())
    return text[:limit]


def _canonical_key(key: object) -> str:
    raw = str(key or "").strip().lower().replace("-", "_")
    if raw not in _ALLOWED_KEYS:
        return ""
    return _KEY_ALIASES.get(raw, raw)


def _from_query(payload: str) -> dict[str, str]:
    if "=" not in payload:
        return {}
    parsed = parse_qs(payload, keep_blank_values=False)
    out: dict[str, str] = {}
    for key, values in parsed.items():
        canonical = _canonical_key(key)
        if not canonical or not values:
            continue
        value = _clean(values[0])
        if value:
            out[canonical] = value
    return out


def _from_json(payload: str) -> dict[str, str]:
    if not payload.startswith("{"):
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in parsed.items():
        canonical = _canonical_key(key)
        cleaned = _clean(value)
        if canonical and cleaned:
            out[canonical] = cleaned
    return out


def _from_tokens(payload: str) -> dict[str, str]:
    out: dict[str, str] = {}
    normalized = payload.replace("--", "__").replace("|", "__")
    for token in [part.strip() for part in normalized.split("__") if part.strip()]:
        lowered = token.lower().replace("-", "_")
        for prefix, key in _TOKEN_PREFIXES:
            if lowered.startswith(prefix):
                value = _clean(token[len(prefix):])
                if value:
                    out[key] = value
                break
    return out


def _from_short_ad_link(payload: str) -> dict[str, str]:
    if not payload.startswith("ad_"):
        return {}
    try:
        from services.admin_ad_links import resolve_ad_link_payload
    except ImportError:
        return {}
    try:
        resolved = resolve_ad_link_payload(payload)
    except (RuntimeError, OSError, TypeError, ValueError):
        return {}
    if not resolved:
        return {}
    return {str(key): _clean(value) for key, value in resolved.items() if _clean(value)}


def start_attribution_meta(payload: str | None) -> dict[str, str]:
    """Return safe attribution metadata extracted from Telegram /start payload.

    Supported formats:
    - DB-backed short ad IDs: ad_123
    - query-like: utm_source=telegram_ads&utm_campaign=may&utm_creative=video1&ad_spend=340
    - JSON-like: {"utm_source":"telegram_ads","utm_campaign":"may"}
    - Telegram-safe tokens: src_telegram_ads__camp_may__creative_video1__cost_340rub
    """
    raw = _clean(payload or "", limit=512)
    meta: dict[str, str] = {"payload": raw} if raw else {}
    if not raw:
        return meta

    for source in (_from_short_ad_link(raw), _from_json(raw), _from_query(raw), _from_tokens(raw)):
        meta.update(source)

    # Normalize user-facing aliases for admin reports that already look for these fields.
    if "utm_source" in meta:
        meta["source"] = meta["utm_source"]
    if "utm_campaign" in meta:
        meta["campaign"] = meta["utm_campaign"]
    if "utm_creative" in meta:
        meta["creative"] = meta["utm_creative"]
    return meta
