from __future__ import annotations

import json
from typing import Any

_EVENT_TO_CONVERSION = {
    "demo_ack": "demo_ack",
    "sub_menu_open": "tariff_open",
}


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_event_meta(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if not text or not text.startswith("{"):
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def supported_event_names() -> tuple[str, ...]:
    return tuple(sorted(_EVENT_TO_CONVERSION))


def map_event_to_conversion(
    event_row: dict[str, Any],
    *,
    attribution: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    event_id = safe_int(event_row.get("id"))
    name = str(event_row.get("name") or event_row.get("event") or "").strip().lower()
    conversion_type = _EVENT_TO_CONVERSION.get(name)
    if event_id <= 0 or conversion_type is None:
        return None

    meta = parse_event_meta(event_row.get("meta") or event_row.get("payload"))
    return {
        "conversion_type": conversion_type,
        "source_platform": "telegram",
        "source_event": name,
        "external_event_id": f"events:{event_id}",
        "user_id": safe_int(event_row.get("user_id")),
        "amount_minor": 0,
        "currency": "RUB",
        "attribution": dict(attribution or {}),
        "payload": {
            "event_id": event_id,
            "event_created_at": str(event_row.get("created_at") or event_row.get("ts") or ""),
            "event_meta": meta,
        },
        "target_provider": "none",
    }
