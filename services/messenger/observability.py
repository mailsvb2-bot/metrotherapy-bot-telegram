from __future__ import annotations

import logging
from typing import Any

from services.events import log_runtime_event

log = logging.getLogger(__name__)


def _safe_meta(**meta: Any) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in meta.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value
        else:
            safe[key] = str(value)
    return safe


def log_button_rendered(
    *,
    platform: str,
    user_id: int | None,
    surface: str,
    command: str,
    label: str,
) -> None:
    log.info(
        "messenger_button_rendered platform=%s user_id=%s surface=%s command=%s label=%r",
        platform,
        user_id,
        surface,
        command,
        label,
    )
    if user_id is None:
        return
    log_runtime_event(
        int(user_id),
        event_type="messenger_button_rendered",
        source=str(platform or "messenger"),
        payload=_safe_meta(surface=surface, command=command, label=label),
    )


def log_payload_normalized(
    *,
    platform: str,
    user_id: int | None,
    raw_text: str,
    normalized_text: str,
    event_key: str | None = None,
) -> None:
    log.info(
        "messenger_payload_normalized platform=%s user_id=%s raw=%r normalized=%r event_key=%s",
        platform,
        user_id,
        raw_text[:120],
        normalized_text[:120],
        event_key,
    )
    if user_id is None:
        return
    log_runtime_event(
        int(user_id),
        event_type="messenger_payload_normalized",
        source=str(platform or "messenger"),
        payload=_safe_meta(raw_text=raw_text[:120], normalized_text=normalized_text[:120], event_key=event_key),
    )


def log_action_completed(
    *,
    platform: str,
    user_id: int,
    action: str,
    replies: int,
    status: str = "ok",
) -> None:
    log.info(
        "messenger_action_completed platform=%s user_id=%s action=%s replies=%s status=%s",
        platform,
        user_id,
        action,
        replies,
        status,
    )
    log_runtime_event(
        int(user_id),
        event_type="messenger_action_completed",
        source=str(platform or "messenger"),
        payload=_safe_meta(action=action, replies=int(replies), status=status),
    )
