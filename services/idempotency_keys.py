from __future__ import annotations
# Single source of truth for idempotency keys.
# IMPORTANT: This module must stay backward-compatible. Different parts of the project
# historically called helpers with different signatures (session_id vs (user_id,date,slot)).
# We support both to avoid regressions while keeping canonical keys.


import time
import hashlib
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

def for_demo_click(user_id: int | None = None, ts_sec: int | None = None) -> int:
    """Anti-spam idempotency bucket for demo button clicks.
    Old code calls it as `for_demo_click()` (no args) and expects an int bucket.
    New code may pass explicit user_id/ts_sec.
    """
    if ts_sec is None:
        ts_sec = int(time.time())
    if user_id is None:
        # bucket only by time (legacy)
        return int(ts_sec)
    # bucket by user + time (stronger, still int)
    return _stable_int_from_str(f"demo_click:{user_id}:{ts_sec}")

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
