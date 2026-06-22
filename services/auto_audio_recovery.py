from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from services.db import db, mark_delivery_once, unmark_delivery, was_delivered

DEFAULT_STALE_LOCK_SECONDS = 15 * 60
_AUTO_AUDIO_LOCK_STAGES = {"pre_score_lock", "audio_lock"}


@dataclass(frozen=True)
class DeliveryLockDecision:
    acquired: bool
    key: str
    reason: str
    stale_reclaimed: bool = False


@dataclass(frozen=True)
class AutoAudioStaleLock:
    user_id: int
    key: str
    kind: str
    stage: str
    scheduled_at: str
    age_seconds: int
    created_at: int


def _stale_after_seconds(value: int | None = None) -> int:
    if value is not None:
        return max(1, int(value))
    raw = (os.getenv("AUTO_AUDIO_STALE_LOCK_SECONDS") or "").strip()
    if not raw:
        return DEFAULT_STALE_LOCK_SECONDS
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_STALE_LOCK_SECONDS


def _delivery_key(*parts: Any) -> str:
    cleaned = [str(part).strip() for part in parts if str(part).strip()]
    if not cleaned:
        raise ValueError("delivery idempotency key must not be empty")
    return ":".join(cleaned)


def _parse_lock_key(key: str) -> tuple[str, str, str]:
    parts = str(key or "").split(":", 2)
    if len(parts) != 3:
        return "", "", str(key or "")
    return parts[0], parts[1], parts[2]


def acquire_delivery_lock(
    user_id: int,
    kind: str,
    stage: str,
    scheduled_at: str,
    *,
    final_stage: str | None,
    stale_after_seconds: int | None = None,
) -> DeliveryLockDecision:
    """Acquire a delivery lock and reclaim it when it is stale.

    The normal idempotency flow is two-phase:
    lock stage -> external send -> final stage -> lock cleanup.

    If the process dies after writing the lock but before cleanup/final marker, a
    plain INSERT OR IGNORE would block the user forever. This helper keeps the
    final marker authoritative and only reclaims an old lock when no final marker
    exists.
    """
    user_id = int(user_id)
    kind = str(kind or "").strip()
    stage = str(stage or "").strip()
    scheduled_at = str(scheduled_at or "").strip()
    lock_key = _delivery_key(kind, stage, scheduled_at)
    if final_stage and was_delivered(user_id, kind, final_stage, scheduled_at):
        return DeliveryLockDecision(False, lock_key, "final_marker_exists")

    if mark_delivery_once(user_id, kind, stage, scheduled_at):
        return DeliveryLockDecision(True, lock_key, "created")

    threshold = _stale_after_seconds(stale_after_seconds)
    now = int(time.time())
    with db() as conn:
        row = conn.execute(
            "SELECT created_at FROM idempotency WHERE user_id=? AND key=? LIMIT 1",
            (user_id, lock_key),
        ).fetchone()
    if not row:
        if mark_delivery_once(user_id, kind, stage, scheduled_at):
            return DeliveryLockDecision(True, lock_key, "created_after_missing")
        return DeliveryLockDecision(False, lock_key, "duplicate_without_row")

    try:
        created_at = int(row["created_at"] if hasattr(row, "keys") else row[0])
    except TypeError:
        return DeliveryLockDecision(False, lock_key, "duplicate_unreadable_created_at")
    except ValueError:
        return DeliveryLockDecision(False, lock_key, "duplicate_unreadable_created_at")

    if created_at > now - threshold:
        return DeliveryLockDecision(False, lock_key, "duplicate_fresh_lock")

    if final_stage and was_delivered(user_id, kind, final_stage, scheduled_at):
        return DeliveryLockDecision(False, lock_key, "final_marker_exists")

    unmark_delivery(user_id, kind, stage, scheduled_at)
    if mark_delivery_once(user_id, kind, stage, scheduled_at):
        return DeliveryLockDecision(True, lock_key, "stale_reclaimed", stale_reclaimed=True)
    return DeliveryLockDecision(False, lock_key, "stale_reclaim_race")


def list_stale_auto_audio_locks(*, stale_after_seconds: int | None = None, limit: int = 20) -> list[AutoAudioStaleLock]:
    threshold = _stale_after_seconds(stale_after_seconds)
    now = int(time.time())
    cutoff = now - threshold
    with db() as conn:
        rows = conn.execute(
            """
            SELECT user_id, key, created_at
            FROM idempotency
            WHERE created_at <= ?
              AND (key LIKE ? OR key LIKE ?)
            ORDER BY created_at ASC
            LIMIT ?
            """.strip(),
            (cutoff, "%:pre_score_lock:%", "%:audio_lock:%", int(limit)),
        ).fetchall()

    out: list[AutoAudioStaleLock] = []
    for row in rows:
        key = str(row["key"] if hasattr(row, "keys") else row[1])
        kind, stage, scheduled_at = _parse_lock_key(key)
        if stage not in _AUTO_AUDIO_LOCK_STAGES:
            continue
        created_at = int(row["created_at"] if hasattr(row, "keys") else row[2])
        user_id = int(row["user_id"] if hasattr(row, "keys") else row[0])
        out.append(
            AutoAudioStaleLock(
                user_id=user_id,
                key=key,
                kind=kind,
                stage=stage,
                scheduled_at=scheduled_at,
                age_seconds=max(0, now - created_at),
                created_at=created_at,
            )
        )
    return out


def release_stale_auto_audio_locks(*, stale_after_seconds: int | None = None, limit: int = 20, dry_run: bool = True) -> int:
    locks = list_stale_auto_audio_locks(stale_after_seconds=stale_after_seconds, limit=limit)
    if dry_run:
        return len(locks)
    released = 0
    with db() as conn:
        for lock in locks:
            cur = conn.execute(
                "DELETE FROM idempotency WHERE user_id=? AND key=?",
                (int(lock.user_id), str(lock.key)),
            )
            released += max(int(getattr(cur, "rowcount", 0) or 0), 0)
    return released


def auto_audio_lock_summary(*, stale_after_seconds: int | None = None, limit: int = 10) -> dict[str, Any]:
    locks = list_stale_auto_audio_locks(stale_after_seconds=stale_after_seconds, limit=limit)
    return {
        "stale_lock_count": len(locks),
        "locks": [
            {
                "user_id": lock.user_id,
                "kind": lock.kind,
                "stage": lock.stage,
                "scheduled_at": lock.scheduled_at,
                "age_seconds": lock.age_seconds,
                "key": lock.key,
            }
            for lock in locks
        ],
    }


def format_auto_audio_lock_report(*, stale_after_seconds: int | None = None, limit: int = 10) -> str:
    summary = auto_audio_lock_summary(stale_after_seconds=stale_after_seconds, limit=limit)
    lines = ["Auto-audio delivery locks", f"Stale locks: {summary['stale_lock_count']}"]
    for item in summary["locks"]:
        lines.append(
            "- "
            f"user_id={item['user_id']} "
            f"stage={item['stage']} "
            f"kind={item['kind']} "
            f"age={item['age_seconds']}s "
            f"scheduled_at={item['scheduled_at']}"
        )
    if not summary["locks"]:
        lines.append("- no stale auto-audio locks")
    return "\n".join(lines)


__all__ = [
    "AutoAudioStaleLock",
    "DeliveryLockDecision",
    "acquire_delivery_lock",
    "auto_audio_lock_summary",
    "format_auto_audio_lock_report",
    "list_stale_auto_audio_locks",
    "release_stale_auto_audio_locks",
]
