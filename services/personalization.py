from __future__ import annotations
import logging


import json
import time
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Optional

from services.db import db
from services.behavior import get_behavior


CORE_LINE = "переобучение нервной системы через ритм повседневности"


_PREFACE_CACHE_TTL_SEC = 300.0
_BRICK_LOG_TTL_SEC = 6 * 3600.0
_preface_cache: dict[tuple[int, str], tuple[float, str]] = {}
_preface_cache_lock = Lock()
_brick_log_guard: dict[tuple[int, str, str], float] = {}
_brick_log_guard_lock = Lock()


def _remember_preface(user_id: int, context: str, text: str) -> str:
    now_m = time.monotonic()
    with _preface_cache_lock:
        _preface_cache[(int(user_id), str(context))] = (now_m, text)
        if len(_preface_cache) > 4096:
            cutoff = now_m - _PREFACE_CACHE_TTL_SEC
            stale = [k for k, (ts, _) in _preface_cache.items() if ts < cutoff]
            for key in stale[:2048]:
                _preface_cache.pop(key, None)
    return text


def _cached_preface(user_id: int, context: str) -> str | None:
    now_m = time.monotonic()
    with _preface_cache_lock:
        hit = _preface_cache.get((int(user_id), str(context)))
        if not hit:
            return None
        ts, value = hit
        if now_m - ts > _PREFACE_CACHE_TTL_SEC:
            _preface_cache.pop((int(user_id), str(context)), None)
            return None
        return value


def _should_log_preface_brick(user_id: int, brick_key: str, context: str) -> bool:
    now_m = time.monotonic()
    guard_key = (int(user_id), str(brick_key), str(context))
    with _brick_log_guard_lock:
        last = _brick_log_guard.get(guard_key)
        if last is not None and (now_m - last) < _BRICK_LOG_TTL_SEC:
            return False
        _brick_log_guard[guard_key] = now_m
        if len(_brick_log_guard) > 8192:
            cutoff = now_m - _BRICK_LOG_TTL_SEC
            stale = [k for k, ts in _brick_log_guard.items() if ts < cutoff]
            for key in stale[:4096]:
                _brick_log_guard.pop(key, None)
        return True


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat()


def _log_brick(user_id: int, brick_key: str, context: str | None = None) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO user_bricks(user_id, brick_key, context, ts) VALUES(?,?,?,?)",
            (int(user_id), brick_key, context, _utc_now_iso()),
        )


def get_preface(user_id: int, context: str = "generic") -> str:
    """Returns a short 2-step block: first attunement, then gentle correction.

    Hot path: main menu and several callback screens call this often.
    We keep it cheap by caching per-user/context text for a short TTL and
    rate-limiting decorative brick writes.
    """

    if str(context) in {"menu", "messenger_menu", "vk_menu", "max_menu"}:
        # Messenger menus already contain the canonical welcome and action list.
        # A behavioral preface before that welcome duplicates the first-screen copy
        # in VK/MAX and makes the entry message too noisy.
        return _remember_preface(int(user_id), context, "")

    cached = _cached_preface(int(user_id), context)
    if cached is not None:
        return cached

    b = get_behavior(int(user_id))
    profile = (b.profile or "stable").strip()

    if profile == "compressed":
        attune = "Возможно, сейчас Вам важно чуть больше опоры и ясности. Я подстроюсь под Ваш темп."
        correct = "Если Вы просто едете и слушаете — нервная система уже начинает перестраиваться через ритм повседневности."
        brick = "preface:compressed"
    elif profile == "sparse":
        attune = "Скорее всего, Вам сейчас подходит более мягкий, бережный темп. Я подстроюсь под Ваш ритм."
        correct = "Здесь ничего не нужно делать специально: Вы просто едете — и с Вами происходит работа."
        brick = "preface:sparse"
    else:
        attune = "Вероятно, Вам комфортен ровный темп. Я подстроюсь под Ваш ритм."
        correct = "Метротерапия — это переобучение нервной системы через ритм повседневности: Вы просто едете — и с Вами происходит работа."
        brick = "preface:stable"

    if _should_log_preface_brick(int(user_id), brick, context):
        _log_brick(int(user_id), brick, context)
    return _remember_preface(int(user_id), context, f"{attune}\n\n{correct}\n")


def choose_variant(user_id: int) -> str:
    """A/B/C variant for funnels based on rhythm profile."""
    p = (get_behavior(int(user_id)).profile or "stable")
    return {"compressed": "A", "sparse": "B", "stable": "C"}.get(p, "C")


def set_funnel_stage(user_id: int, stage: str, variant: Optional[str] = None) -> None:
    now = _utc_now_iso()
    variant = variant or choose_variant(user_id)
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_funnel(user_id, stage, variant, updated_at) VALUES(?,?,?,?)",
            (int(user_id), stage, variant, now),
        )
        conn.execute(
            "UPDATE user_funnel SET stage=?, variant=?, updated_at=? WHERE user_id=?",
            (stage, variant, now, int(user_id)),
        )


def get_funnel(user_id: int) -> dict:
    with db() as conn:
        row = conn.execute("SELECT stage, variant, updated_at FROM user_funnel WHERE user_id=?", (int(user_id),)).fetchone()
    if not row:
        return {"stage": None, "variant": choose_variant(user_id), "updated_at": None}
    return {"stage": row["stage"], "variant": row["variant"], "updated_at": row["updated_at"]}


def should_offer_micro_question(user_id: int) -> Optional[str]:
    """Returns q_key if we should ask a micro-question now."""
    # Ask at most once per 24h and max 3 total answers.
    with db() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM micro_answers WHERE user_id=?", (int(user_id),)).fetchone()[0]
        if cnt >= 3:
            return None

        last = conn.execute(
            "SELECT ts FROM micro_answers WHERE user_id=? ORDER BY ts DESC LIMIT 1",
            (int(user_id),),
        ).fetchone()

        if last and last[0]:
            try:
                ts = datetime.fromisoformat(str(last[0]).replace("Z", "+00:00"))
                if _utc_now() - ts < timedelta(hours=24):
                    return None
            except ValueError:
                logging.getLogger(__name__).exception("Unhandled exception")

        # pick first active question that user hasn't answered
        rows = conn.execute(
            """
            SELECT q.key
            FROM micro_questions q
            WHERE q.is_active=1
              AND NOT EXISTS(
                SELECT 1 FROM micro_answers a WHERE a.user_id=? AND a.q_key=q.key
              )
            ORDER BY q.key
            LIMIT 1
            """,
            (int(user_id),),
        ).fetchall()
        if not rows:
            return None
        return str(rows[0]["key"])


def get_micro_question(q_key: str) -> Optional[dict]:
    with db() as conn:
        row = conn.execute(
            "SELECT key, question, options FROM micro_questions WHERE key=? AND is_active=1",
            (str(q_key),),
        ).fetchone()
    if not row:
        return None
    try:
        opts = json.loads(row["options"])
    except (json.JSONDecodeError, TypeError, ValueError):
        logging.getLogger(__name__).exception("Failed to parse micro question options for %s", q_key)
        opts = []
    return {"key": row["key"], "question": row["question"], "options": opts}


def save_micro_answer(user_id: int, q_key: str, answer: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO micro_answers(user_id, q_key, answer, ts) VALUES(?,?,?,?)",
            (int(user_id), str(q_key), str(answer), _utc_now_iso()),
        )
    _log_brick(int(user_id), f"micro_answer:{q_key}", context="micro")
