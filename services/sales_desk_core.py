from __future__ import annotations

import json
from typing import Any

SALES_STAGES: tuple[str, ...] = (
    "new",
    "contacted",
    "qualified",
    "checkout",
    "won",
    "lost",
)

OPEN_STAGES: tuple[str, ...] = (
    "new",
    "contacted",
    "qualified",
    "checkout",
)

_STAGE_RANK = {
    "new": 0,
    "contacted": 1,
    "qualified": 2,
    "checkout": 3,
    "won": 4,
    "lost": -1,
}

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"contacted", "qualified", "lost"}),
    "contacted": frozenset({"qualified", "checkout", "lost"}),
    "qualified": frozenset({"checkout", "won", "lost"}),
    "checkout": frozenset({"qualified", "won", "lost"}),
    "won": frozenset({"qualified"}),
    "lost": frozenset({"new"}),
}

_FILTERS = frozenset(
    {
        "open",
        "new",
        "contacted",
        "qualified",
        "checkout",
        "won",
        "lost",
        "overdue",
        "mine",
        "unassigned",
    }
)

_EVENT_STAGE = {
    "funnel_start_command": "new",
    "demo_sent": "new",
    "demo_ack": "contacted",
    "sub_menu_open": "qualified",
    "funnel_tariffs_command": "qualified",
    "payment_started": "checkout",
    "payment_success": "won",
    "gift_paid": "won",
}


def normalize_stage(value: str | None, *, default: str = "new") -> str:
    stage = str(value or "").strip().lower()
    if stage in SALES_STAGES:
        return stage
    if default not in SALES_STAGES:
        raise ValueError("invalid_default_sales_stage")
    return default


def normalize_filter(value: str | None) -> str:
    normalized = str(value or "open").strip().lower()
    return normalized if normalized in _FILTERS else "open"


def stage_rank(value: str | None) -> int:
    return int(_STAGE_RANK[normalize_stage(value)])


def can_transition(current_stage: str, target_stage: str) -> bool:
    current = normalize_stage(current_stage)
    target = normalize_stage(target_stage)
    return target in _ALLOWED_TRANSITIONS[current]


def assert_transition(current_stage: str, target_stage: str) -> None:
    if not can_transition(current_stage, target_stage):
        raise ValueError(
            f"sales_stage_transition_not_allowed:{normalize_stage(current_stage)}:{normalize_stage(target_stage)}"
        )


def stage_from_event_names(event_names: list[str] | tuple[str, ...]) -> str:
    best = "new"
    for raw_name in event_names:
        candidate = _EVENT_STAGE.get(str(raw_name or "").strip())
        if candidate is None:
            continue
        if candidate == "won":
            return "won"
        if stage_rank(candidate) > stage_rank(best):
            best = candidate
    return best


def should_auto_advance(current_stage: str, incoming_stage: str, *, stage_source: str) -> bool:
    current = normalize_stage(current_stage)
    incoming = normalize_stage(incoming_stage)
    if incoming == "won" and current != "won":
        return True
    if str(stage_source or "auto").strip().lower() != "auto":
        return False
    if current == "lost":
        return False
    return stage_rank(incoming) > stage_rank(current)


def lead_key(user_id: int) -> str:
    normalized = int(user_id)
    if normalized == 0:
        raise ValueError("sales_lead_user_id_must_not_be_zero")
    return f"user:{normalized}"


def compact_display_name(*, first_name: Any = None, username: Any = None, user_id: Any = None) -> str:
    name = str(first_name or "").strip()
    handle = str(username or "").strip().lstrip("@")
    if name and handle:
        return f"{name} (@{handle})"[:160]
    if name:
        return name[:160]
    if handle:
        return f"@{handle}"[:160]
    try:
        return f"Пользователь {int(user_id)}"
    except (TypeError, ValueError):
        return "Пользователь"


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if value is None:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def extract_attribution(*values: Any) -> dict[str, str]:
    merged: dict[str, Any] = {}
    for value in values:
        merged.update(_json_object(value))

    nested = merged.get("attribution")
    if isinstance(nested, dict):
        merged = {**merged, **nested}

    return {
        "source": str(merged.get("source") or merged.get("utm_source") or "organic").strip()[:100] or "organic",
        "campaign": str(merged.get("campaign") or merged.get("utm_campaign") or "").strip()[:160],
        "creative": str(merged.get("creative") or merged.get("utm_content") or "").strip()[:160],
    }


def sanitize_note(value: str, *, max_length: int = 1500) -> str:
    note = " ".join(str(value or "").split())
    if not note:
        raise ValueError("sales_note_empty")
    return note[: max(1, int(max_length))]
