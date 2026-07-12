from __future__ import annotations

"""Sequence-aware facade over the mature full-series progress core."""

from pathlib import Path

from services.messenger import audio_progress_legacy as _legacy
from services.messenger.audio_progress_legacy import *  # noqa: F403

SEQUENCE_FULL_SERIES = _legacy.SEQUENCE_FULL_SERIES
AudioProgressItem = _legacy.AudioProgressItem
AudioProgressSnapshot = _legacy.AudioProgressSnapshot


def get_pending_audio_item(
    user_id: int,
    *,
    sequence_key: str = SEQUENCE_FULL_SERIES,
) -> AudioProgressItem | None:
    """Resolve pending media from its own sequence, never from another catalog.

    The full route may resolve an anchor against ``audio/full``. Demo and future
    independent sequences must use their persisted path/title; anchor 1 in demo
    is not the same media as anchor 1 in the paid full-series catalog.
    """

    last = _legacy.get_last_progress(int(user_id), sequence_key=sequence_key)
    pending_anchor = last.get("pending_anchor")
    if pending_anchor is None:
        return None

    try:
        anchor = int(pending_anchor)
    except (TypeError, ValueError):
        return None

    if sequence_key == SEQUENCE_FULL_SERIES:
        item = _legacy.get_audio_item_by_anchor(anchor)
        if item is not None:
            return item

    pending_path = Path(str(last.get("pending_path") or ""))
    return AudioProgressItem(
        ordinal=0,
        anchor=anchor,
        title=str(
            last.get("pending_title")
            or pending_path.stem
            or f"Audio {anchor}"
        ),
        path=pending_path,
    )


def confirm_pending_audio_delivery(
    user_id: int,
    *,
    platform: str | None = None,
    sequence_key: str = SEQUENCE_FULL_SERIES,
) -> AudioProgressItem | None:
    pending = get_pending_audio_item(int(user_id), sequence_key=sequence_key)
    if pending is None:
        return None
    last = _legacy.get_last_progress(int(user_id), sequence_key=sequence_key)
    resolved_platform = str(
        platform
        or last.get("pending_platform")
        or last.get("last_platform")
        or "telegram"
    )
    _legacy.record_audio_delivery(
        int(user_id),
        item=pending,
        platform=resolved_platform,
        sequence_key=sequence_key,
    )
    _legacy.log_audio_timeline_event(
        int(user_id),
        event_type="manual_confirmed",
        sequence_key=sequence_key,
        anchor=int(pending.anchor),
        title=pending.title,
        platform=resolved_platform,
    )
    return pending


def get_progress_snapshot(
    user_id: int,
    *,
    sequence_key: str = SEQUENCE_FULL_SERIES,
) -> AudioProgressSnapshot:
    last = _legacy.get_last_progress(int(user_id), sequence_key=sequence_key)
    pending_item = get_pending_audio_item(int(user_id), sequence_key=sequence_key)
    next_item = (
        pending_item or _legacy.get_next_audio_item(int(user_id), sequence_key=sequence_key)
        if sequence_key == SEQUENCE_FULL_SERIES
        else pending_item
    )
    last_anchor = last.get("last_anchor")
    return AudioProgressSnapshot(
        user_id=int(user_id),
        sequence_key=sequence_key,
        last_anchor=int(last_anchor) if last_anchor is not None else None,
        last_title=str(last.get("last_title")) if last.get("last_title") is not None else None,
        last_platform=str(last.get("last_platform")) if last.get("last_platform") is not None else None,
        last_confirmed_at=(
            str(last.get("last_confirmed_at"))
            if last.get("last_confirmed_at") is not None
            else None
        ),
        pending_item=pending_item,
        pending_platform=(
            str(last.get("pending_platform"))
            if last.get("pending_platform") is not None
            else None
        ),
        pending_delivered_at=(
            str(last.get("pending_delivered_at"))
            if last.get("pending_delivered_at") is not None
            else None
        ),
        next_item=next_item,
    )
