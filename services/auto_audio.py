from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TypedDict
from zoneinfo import ZoneInfo

TELEGRAM_API_ERROR: type[BaseException]

try:
    from aiogram import Bot
    from aiogram.exceptions import TelegramAPIError as _TelegramAPIError
except ImportError:  # pragma: no cover
    Bot = object  # type: ignore[misc,assignment]
    TELEGRAM_API_ERROR = RuntimeError
else:
    TELEGRAM_API_ERROR = _TelegramAPIError

from config.settings import settings
from core.runtime_env import env_int
from runtime.messenger_senders import MaxBotSender, TelegramBotSender, VkBotSender
from services.audio_anchor import pick_for_slot
from services.auto_audio_entitlement import eligible_user_ids, has_entitlement
from services.auto_audio_recovery import acquire_delivery_lock
from services.db import db, mark_delivery_once, unmark_delivery, was_delivered
from services.delivery_preferences import DeliveryPolicyDecision, build_delivery_policy_decision
from services.events import log_event
from services.idempotency_keys import for_pre_score
from services.messenger.outbound import SenderRegistry, build_delivery_plan
from services.mood import create_session
from services.progress import get_index

log = logging.getLogger(__name__)


class DueCandidate(TypedDict):
    uid: int
    slot: str
    policy: DeliveryPolicyDecision
    hm: str
    scheduled_now: bool


def _norm_hms(hm: str) -> tuple[int, int, int]:
    hm = (hm or "").strip()
    parts = hm.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, TypeError, IndexError):
        return (0, 0, 0)
    return (max(0, min(23, h)), max(0, min(59, m)), max(0, min(59, s)))


def _plus_one_sec(h: int, m: int, s: int) -> tuple[int, int, int]:
    s += 1
    if s >= 60:
        s = 0
        m += 1
    if m >= 60:
        m = 0
        h = (h + 1) % 24
    return h, m, s


def _prompt_due_at(local_dt: datetime, hm: str) -> datetime:
    h, m, s = _norm_hms(hm)
    selected = local_dt.replace(hour=h, minute=m, second=s, microsecond=0)
    return selected + timedelta(seconds=1)


def _matches_slot_second(local_dt: datetime, hm: str) -> bool:
    h, m, s = _norm_hms(hm)
    th, tm, ts = _plus_one_sec(h, m, s)
    return f"{th:02d}:{tm:02d}:{ts:02d}" == local_dt.strftime("%H:%M:%S")


def _is_due_local_day(local_dt: datetime, hm: str) -> bool:
    due_at = _prompt_due_at(local_dt, hm)
    return local_dt.date() == due_at.date() and local_dt >= due_at


def _slot_time_for_user(uid: int, slot: str) -> str:
    default_hm = settings.MORNING_TIME if slot == "morning" else settings.EVENING_TIME
    default_hm = (default_hm or ("08:30" if slot == "morning" else "19:00")).strip()
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT work_time, home_time FROM users WHERE user_id=?",
                (int(uid),),
            ).fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return default_hm
    value = row["work_time"] if slot == "morning" else row["home_time"]
    return (value or default_hm).strip()


def _is_due_for_user(uid: int, slot: str, now_utc: datetime) -> tuple[bool, str, str]:
    """Point preflight for diagnostics and focused delivery probes.

    Bulk runtime delivery uses ``eligible_user_ids`` to avoid an N+1 entitlement
    query storm. Individual probes retain the canonical ``has_entitlement`` guard
    so no direct caller can bypass the practice-wallet/subscription source of truth.
    """

    policy = build_delivery_policy_decision(int(uid), slot, now_utc=now_utc)
    hm = _slot_time_for_user(uid, slot)
    local_now = now_utc.astimezone(ZoneInfo(policy.timezone))
    if not has_entitlement(int(uid), slot):
        return False, policy.timezone, hm
    if policy.blocked_by_quiet_hours:
        return False, policy.timezone, hm
    return _is_due_local_day(local_now, hm), policy.timezone, hm


def _collect_due_candidates(now_utc: datetime) -> list[DueCandidate]:
    out: list[DueCandidate] = []
    for slot in ("morning", "evening"):
        # eligible_user_ids() already applies the canonical practice-wallet or
        # subscription entitlement in bulk. Do not repeat has_entitlement() here:
        # that would recreate the old per-user DB query storm.
        for uid in eligible_user_ids(slot):
            policy = build_delivery_policy_decision(uid, slot, now_utc=now_utc)
            hm = _slot_time_for_user(uid, slot)
            local_now = now_utc.astimezone(ZoneInfo(policy.timezone))
            scheduled_now = _is_due_local_day(local_now, hm)
            if not scheduled_now:
                continue
            out.append(
                {
                    "uid": int(uid),
                    "slot": slot,
                    "policy": policy,
                    "hm": hm,
                    "scheduled_now": scheduled_now,
                }
            )
    return out


async def _send_pre_prompt(
    bot: Bot,
    uid: int,
    *,
    session_id: int,
    channel: str,
    senders: SenderRegistry,
) -> None:
    prompt = (
        "📍 Перед прослушиванием оцените своё состояние сейчас по шкале от -10 до +10.\n\n"
        "Просто ответьте одним числом, например: 3 или -2.\n"
        "После этого я сразу пришлю ваш аудиотранс."
    )
    if channel == "telegram":
        from keyboards.inline import kb_mood_scale

        await bot.send_message(
            uid,
            "📍 Перед прослушиванием: оцените своё состояние сейчас (−10 … +10):\n\n"
            "Нажмите оценку — и я сразу пришлю Вам аудиотранс.",
            reply_markup=kb_mood_scale(session_id, stage="pre"),
        )
        return
    plan = build_delivery_plan(int(uid), preferred_platform=channel, fallback=channel)
    sender = senders.get(channel)
    if sender is None or not plan.external_user_id:
        raise RuntimeError(f"No sender/external id for channel={channel}")
    await sender.send_text(plan.external_user_id, prompt)


async def _unmark_pre_score_lock(uid: int, kind: str, scheduled_at: str) -> None:
    try:
        await asyncio.to_thread(unmark_delivery, uid, kind, "pre_score_lock", scheduled_at)
    except sqlite3.Error:
        log.debug("pre_score_lock idempotency cleanup failed", exc_info=True)


def _safe_error_meta(exc: BaseException) -> dict[str, str]:
    return {"error_type": type(exc).__name__}


async def _process_due_candidate(
    bot: Bot,
    item: DueCandidate,
    *,
    now_utc: datetime,
    senders: SenderRegistry,
) -> None:
    uid = int(item["uid"])
    slot = str(item["slot"])
    policy = item["policy"]
    if policy.blocked_by_quiet_hours:
        log_event(
            uid,
            "auto_audio_quiet_hours_block",
            {
                "slot": slot,
                "tz": policy.timezone,
                "preferred": policy.preferred_channel,
                "resolved": policy.resolved_channel,
                "next_allowed_at": (
                    policy.next_allowed_at.isoformat() if policy.next_allowed_at else None
                ),
            },
        )
        return

    tz_name = policy.timezone
    idx = await asyncio.to_thread(get_index, uid, slot)
    anchor = pick_for_slot(slot, idx)
    if not anchor:
        return
    local_day = now_utc.astimezone(ZoneInfo(tz_name)).date().isoformat()
    scheduled_at = for_pre_score(uid, local_day, slot)
    kind = "work" if slot == "morning" else "home"
    if await asyncio.to_thread(was_delivered, uid, kind, "pre_score", scheduled_at):
        log_event(
            uid,
            "idempotency_skip",
            {"stage": "pre_score", "slot": slot, "scheduled_at": scheduled_at},
        )
        return

    lock = await asyncio.to_thread(
        acquire_delivery_lock,
        uid,
        kind,
        "pre_score_lock",
        scheduled_at,
        final_stage="pre_score",
    )
    if not lock.acquired:
        log_event(
            uid,
            "idempotency_skip",
            {
                "stage": "pre_score_lock",
                "slot": slot,
                "scheduled_at": scheduled_at,
                "reason": lock.reason,
            },
        )
        return
    if lock.stale_reclaimed:
        log_event(
            uid,
            "auto_audio_stale_lock_reclaimed",
            {"stage": "pre_score_lock", "slot": slot, "scheduled_at": scheduled_at},
        )

    try:
        session_id = await asyncio.to_thread(
            create_session,
            uid,
            kind=kind,
            source="auto",
            day=local_day,
            slot=slot,
            scheduled_at=scheduled_at,
            anchor_id=anchor.anchor,
        )
        await _send_pre_prompt(
            bot,
            uid,
            session_id=session_id,
            channel=policy.resolved_channel,
            senders=senders,
        )
        await asyncio.to_thread(mark_delivery_once, uid, kind, "pre_score", scheduled_at)
        log_event(
            uid,
            "auto_audio_prompted",
            {
                "slot": slot,
                "anchor": anchor.anchor,
                "day": local_day,
                "channel": policy.resolved_channel,
                "preferred": policy.preferred_channel,
                "tz": tz_name,
            },
        )
        if policy.fallback_used:
            log_event(
                uid,
                "auto_audio_channel_fallback",
                {
                    "slot": slot,
                    "preferred": policy.preferred_channel,
                    "resolved": policy.resolved_channel,
                    "tz": tz_name,
                },
            )
    except asyncio.CancelledError:
        raise
    except TELEGRAM_API_ERROR as exc:
        log_event(
            uid,
            "auto_audio_telegram_delivery_error",
            {
                "slot": slot,
                "channel": policy.resolved_channel,
                **_safe_error_meta(exc),
            },
        )
    except (sqlite3.Error, RuntimeError, ValueError, TypeError, OSError, asyncio.TimeoutError) as exc:  # validator: allow-wide-except
        log_event(
            uid,
            "auto_audio_error",
            {
                "slot": slot,
                "channel": policy.resolved_channel,
                **_safe_error_meta(exc),
            },
        )
    finally:
        await _unmark_pre_score_lock(uid, kind, scheduled_at)


async def _run_candidate_workers(
    bot: Bot,
    due_candidates: list[DueCandidate],
    *,
    now_utc: datetime,
    senders: SenderRegistry,
) -> None:
    if not due_candidates:
        return
    worker_count = min(
        len(due_candidates),
        env_int("AUTO_AUDIO_WORKERS", 4, minimum=1, maximum=32),
    )
    queue: asyncio.Queue[DueCandidate] = asyncio.Queue()
    for item in due_candidates:
        queue.put_nowait(item)

    async def _worker() -> None:
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await _process_due_candidate(
                    bot,
                    item,
                    now_utc=now_utc,
                    senders=senders,
                )
            finally:
                queue.task_done()

    async with asyncio.TaskGroup() as task_group:
        for _ in range(worker_count):
            task_group.create_task(_worker())


async def tick(bot: Bot) -> None:
    try:
        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        senders = SenderRegistry(
            telegram=TelegramBotSender(bot),
            max=MaxBotSender(),
            vk=VkBotSender(),
        )
        due_candidates = await asyncio.to_thread(_collect_due_candidates, now_utc)
        await _run_candidate_workers(
            bot,
            due_candidates,
            now_utc=now_utc,
            senders=senders,
        )
    except asyncio.CancelledError:
        raise
    except (sqlite3.Error, RuntimeError, ValueError, TypeError, OSError, asyncio.TimeoutError) as exc:  # validator: allow-wide-except
        log.error("auto_audio.tick failed error_type=%s", type(exc).__name__)
