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
from runtime.messenger_senders import MaxBotSender, TelegramBotSender, VkBotSender
from services.audio_anchor import pick_for_slot
from services.auto_audio_entitlement import eligible_user_ids
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
            row = conn.execute("SELECT work_time, home_time FROM users WHERE user_id=?", (int(uid),)).fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return default_hm
    value = row["work_time"] if slot == "morning" else row["home_time"]
    return (value or default_hm).strip()


def _is_due_for_user(uid: int, slot: str, now_utc: datetime) -> tuple[bool, str, str]:
    policy = build_delivery_policy_decision(int(uid), slot, now_utc=now_utc)
    hm = _slot_time_for_user(uid, slot)
    local_now = now_utc.astimezone(ZoneInfo(policy.timezone))
    if policy.blocked_by_quiet_hours:
        return False, policy.timezone, hm
    return _is_due_local_day(local_now, hm), policy.timezone, hm


def _collect_due_candidates(now_utc: datetime) -> list[DueCandidate]:
    out: list[DueCandidate] = []
    for slot in ("morning", "evening"):
        # eligible_user_ids() already performs final entitlement filtering in bulk.
        # Do not call has_entitlement() here: that recreates the old per-user
        # subscription/wallet DB query storm and slows down audio delivery.
        for uid in eligible_user_ids(slot):
            policy = build_delivery_policy_decision(uid, slot, now_utc=now_utc)
            hm = _slot_time_for_user(uid, slot)
            local_now = now_utc.astimezone(ZoneInfo(policy.timezone))
            scheduled_now = _is_due_local_day(local_now, hm)
            if not scheduled_now:
                continue
            out.append({"uid": int(uid), "slot": slot, "policy": policy, "hm": hm, "scheduled_now": scheduled_now})
    return out


async def _send_pre_prompt(bot: Bot, uid: int, *, session_id: int, channel: str, senders: SenderRegistry) -> None:
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


async def _record_auto_audio_error(uid: int, kind: str, scheduled_at: str, slot: str, channel: str, exc: BaseException) -> None:
    await _unmark_pre_score_lock(uid, kind, scheduled_at)
    log_event(uid, "auto_audio_error", {"slot": slot, "err": str(exc), "channel": channel})


async def tick(bot: Bot):
    try:
        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        senders = SenderRegistry(telegram=TelegramBotSender(bot), max=MaxBotSender(), vk=VkBotSender())
        due_candidates = await asyncio.to_thread(_collect_due_candidates, now_utc)
        for item in due_candidates:
            uid = int(item["uid"])
            slot = str(item["slot"])
            policy = item["policy"]
            if policy.blocked_by_quiet_hours:
                log_event(uid, "auto_audio_quiet_hours_block", {"slot": slot, "tz": policy.timezone, "preferred": policy.preferred_channel, "resolved": policy.resolved_channel, "next_allowed_at": policy.next_allowed_at.isoformat() if policy.next_allowed_at else None})
                continue
            tz_name = policy.timezone
            idx = await asyncio.to_thread(get_index, uid, slot)
            aa = pick_for_slot(slot, idx)
            if not aa:
                continue
            local_day = now_utc.astimezone(ZoneInfo(tz_name)).date().isoformat()
            scheduled_at = for_pre_score(uid, local_day, slot)
            kind = "work" if slot == "morning" else "home"
            if await asyncio.to_thread(was_delivered, uid, kind, "pre_score", scheduled_at):
                log_event(uid, "idempotency_skip", {"stage": "pre_score", "slot": slot, "scheduled_at": scheduled_at})
                continue
            lock = await asyncio.to_thread(
                acquire_delivery_lock,
                uid,
                kind,
                "pre_score_lock",
                scheduled_at,
                final_stage="pre_score",
            )
            if not lock.acquired:
                log_event(uid, "idempotency_skip", {"stage": "pre_score_lock", "slot": slot, "scheduled_at": scheduled_at, "reason": lock.reason})
                continue
            if lock.stale_reclaimed:
                log_event(uid, "auto_audio_stale_lock_reclaimed", {"stage": "pre_score_lock", "slot": slot, "scheduled_at": scheduled_at})
            try:
                sid = await asyncio.to_thread(create_session, uid, kind=kind, source="auto", day=local_day, slot=slot, scheduled_at=scheduled_at, anchor_id=aa.anchor)
                await _send_pre_prompt(bot, uid, session_id=sid, channel=policy.resolved_channel, senders=senders)
                await asyncio.to_thread(mark_delivery_once, uid, kind, "pre_score", scheduled_at)
                await _unmark_pre_score_lock(uid, kind, scheduled_at)
                log_event(uid, "auto_audio_prompted", {"slot": slot, "anchor": aa.anchor, "day": local_day, "channel": policy.resolved_channel, "preferred": policy.preferred_channel, "tz": tz_name})
                if policy.fallback_used:
                    log_event(uid, "auto_audio_channel_fallback", {"slot": slot, "preferred": policy.preferred_channel, "resolved": policy.resolved_channel, "tz": tz_name})
            except TELEGRAM_API_ERROR as exc:
                await _unmark_pre_score_lock(uid, kind, scheduled_at)
                log_event(uid, "auto_audio_telegram_delivery_error", {"slot": slot, "err": str(exc), "channel": policy.resolved_channel})
                continue
            except sqlite3.Error as exc:
                await _record_auto_audio_error(uid, kind, scheduled_at, slot, policy.resolved_channel, exc)
                continue
            except RuntimeError as exc:
                await _record_auto_audio_error(uid, kind, scheduled_at, slot, policy.resolved_channel, exc)
                continue
            except ValueError as exc:
                await _record_auto_audio_error(uid, kind, scheduled_at, slot, policy.resolved_channel, exc)
                continue
            except TypeError as exc:
                await _record_auto_audio_error(uid, kind, scheduled_at, slot, policy.resolved_channel, exc)
                continue
    except sqlite3.Error:
        log.exception("auto_audio.tick database failure")
        return
    except RuntimeError:
        log.exception("auto_audio.tick runtime failure")
        return
    except ValueError:
        log.exception("auto_audio.tick runtime failure")
        return
