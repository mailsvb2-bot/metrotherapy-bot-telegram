from __future__ import annotations

from typing import Any

from services.growth_conversion_event_bridge import event_conversion_bridge_snapshot
from services.growth_conversion_hub import build_conversion_hub_report


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def format_event_bridge_status(snapshot: dict[str, Any]) -> str:
    if not bool(snapshot.get("ok")):
        return "\n".join([
            "🔄 Event bridge",
            "— status: DEGRADED",
            f"— error: {snapshot.get('error') or 'unknown'}",
            "— основной runtime не блокируется",
        ])
    return "\n".join([
        "🔄 Event bridge",
        "— status: OK",
        f"— cursor event_id: {_safe_int(snapshot.get('last_event_id'))}",
        f"— last batch: {_safe_int(snapshot.get('last_batch_size'))}",
        f"— inserted: {_safe_int(snapshot.get('last_inserted'))}",
        f"— duplicates: {_safe_int(snapshot.get('last_duplicates'))}",
        f"— updated_at: {snapshot.get('updated_at') or '—'}",
    ])


def build_growth_conversion_runtime_report(period: str = "today") -> str:
    base = build_conversion_hub_report(period)
    bridge = format_event_bridge_status(event_conversion_bridge_snapshot())
    return base + "\n\n" + bridge
