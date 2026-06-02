from __future__ import annotations
# Single source of truth for idempotency keys.
# IMPORTANT: This module must stay backward-compatible. Different parts of the project
# historically called helpers with different signatures (session_id vs (user_id,date,slot)).
# We support both to avoid regressions while keeping canonical keys.


import hashlib
import inspect
import time
from typing import Any


def delivery_key(user_id: int, date: str, slot: str, stage: str) -> str:
    """Canonical delivery idempotency key."""
    return f"d:{user_id}:{date}:{slot}:{stage}"


def job_key(kind: str, ref_id: str) -> str:
    """Canonical job idempotency key."""
    return f"j:{kind}:{ref_id}"


def _stable_int_from_str(s: str) -> int:
    # Deterministic 31-bit int from string
    h = hashlib.sha1(s.encode("utf-8")).hexdigest()
    return int(h[:8], 16)


# --- Backward-compatible helpers ---


def _caller_session_id() -> int | None:
    """Best-effort bridge for legacy no-arg for_demo_click() callers.

    handlers.mood_flow.ratings historically called for_demo_click() without
    passing the session id, which degraded demo idempotency to a seconds bucket.
    While keeping the public signature compatible, recover the local `sid` /
    `session_id` from the immediate caller when available. This keeps demo audio
    idempotency session-stable without changing callback payloads.
    """
    frame = inspect.currentframe()
    try:
        caller = frame.f_back.f_back if frame and frame.f_back else None
        if caller is None:
            return None
        for name in ("sid", "session_id"):
            raw = caller.f_locals.get(name)
            if raw is None:
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                return _stable_int_from_str(f"session:{raw}")
        return None
    finally:
        # Avoid keeping frame locals alive.
        del frame


def for_demo_click(user_id: int | None = None, ts_sec: int | None = None, session_id: int | None = None) -> int:
    """Idempotency bucket for demo audio clicks.

    Preferred calls:
      - for_demo_click(user_id, session_id=<session_id>)
      - for_session(session_id) for new code paths

    Legacy no-arg calls are supported and now try to recover the surrounding
    session id before falling back to a short wall-clock bucket.
    """
    if session_id is None:
        session_id = _caller_session_id()
    if session_id is not None:
        if user_id is None:
            return for_session(session_id)
        return _stable_int_from_str(f"demo_click:{int(user_id)}:session:{int(session_id)}")

    if ts_sec is None:
        ts_sec = int(time.time())
    if user_id is None:
        # Last-resort legacy fallback only; callers should pass/recover session_id.
        return int(ts_sec // 60)
    # bucket by user + minute (stronger than the historic seconds-only fallback)
    return _stable_int_from_str(f"demo_click:{user_id}:{int(ts_sec) // 60}")


def for_session(*args: Any) -> int:
    """Session key used as `scheduled_at` in mark_delivery_once/was_delivered.
    Supported calls:
      - for_session(session_id)
      - for_session(user_id, date, slot)  (newer style)
    Returns an int (legacy scheduled_at).
    """
    if len(args) == 1:
        try:
            return int(args[0])
        except (TypeError, ValueError):
            return _stable_int_from_str(f"session:{args[0]}")
    if len(args) == 3:
        user_id, date, slot = args
        return _stable_int_from_str(delivery_key(int(user_id), str(date), str(slot), "session"))
    raise TypeError("for_session expects (session_id) or (user_id, date, slot)")


def for_audio(user_id: int, date: str, slot: str, kind: str) -> str:
    return delivery_key(user_id=user_id, date=date, slot=slot, stage=f"audio:{kind}")


def for_audio_lock(user_id: int, date: str, slot: str, kind: str) -> str:
    return delivery_key(user_id=user_id, date=date, slot=slot, stage=f"audio_lock:{kind}")


def for_pre_score(user_id: int, date: str, slot: str) -> str:
    return delivery_key(user_id=user_id, date=date, slot=slot, stage="pre_score")


def for_post_prompt_sent(user_id: int, date: str, slot: str) -> str:
    return delivery_key(user_id=user_id, date=date, slot=slot, stage="post_prompt_sent")


def for_settings_prompt(user_id: int, date: str, slot: str) -> str:
    return delivery_key(user_id=user_id, date=date, slot=slot, stage="settings_prompt")


def for_gift_activation(gift_code: str) -> str:
    return f"d:gift:{gift_code}:activated"


def for_gift_delivery(gift_code: str) -> str:
    return f"d:gift:{gift_code}:delivered"


def for_job_run_at(kind: str, ref_id: str, run_at: int) -> str:
    # stable per (kind, ref_id, run_at) to prevent re-scheduling duplicates
    return f"{job_key(kind, ref_id)}:{int(run_at)}"


def for_job(kind: str, ref_id: str) -> str:
    return job_key(kind, ref_id)
