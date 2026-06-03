from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from aiogram import Bot
except ImportError:  # pragma: no cover
    Bot = object  # type: ignore[misc,assignment]

from config.settings import settings
import asyncio

from services.audio_anchor import pick_for_slot
from services.db import mark_delivery_once, unmark_delivery, db
from services.delivery_preferences import build_delivery_policy_decision
from services.events import log_event
from services.idempotency_keys import for_pre_score
from services.mood import create_session
from services.auto_audio_entitlement import eligible_user_ids, has_entitlement
from services.progress import get_index
from services.messenger.outbound import SenderRegistry, build_delivery_plan
from runtime.messenger_senders import TelegramBotSender, MaxBotSender, VkBotSender


def _norm_hms(hm: str) -> tuple[int, int, int]:
    hm = (hm or '').strip()
    parts = hm.split(':')
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
    return f'{th:02d}:{tm:02d}:{ts:02d}' == local_dt.strftime('%H:%M:%S')


def _is_due_local_day(local_dt: datetime, hm: str) -> bool:
    due_at = _prompt_due_at(local_dt, hm)
    return local_dt.date() == due_at.date() and local_dt >= due_at


def _slot_time_for_user(uid: int, slot: str) -> str:
    default_hm = settings.MORNING_TIME if slot == 'morning' else settings.EVENING_TIME
    default_hm = (default_hm or ('08:30' if slot == 'morning' else '19:00')).strip()
    try:
        with db() as conn:
            row = conn.execute('SELECT work_time, home_time FROM users WHERE user_id=?', (int(uid),)).fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return default_hm
    value = row['work_time'] if slot == 'morning' else row['home_time']
    return (value or default_hm).strip()


def _is_due_for_user(uid: int, slot: str, now_utc: datetime) -> tuple[bool, str, str]:
    policy = build_delivery_policy_decision(int(uid), slot, now_utc=now_utc)
    hm = _slot_time_for_user(uid, slot)
    local_now = now_utc.astimezone(ZoneInfo(policy.timezone))
    if policy.blocked_by_quiet_hours:
        return False, policy.timezone, hm
    return _is_due_local_day(local_now, hm), policy.timezone, hm


def _collect_due_candidates(now_utc: datetime) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for slot in ("morning", "evening"):
        for uid in eligible_user_ids(slot):
            if not has_entitlement(uid, slot):
                continue
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
        '📍 Перед прослушиванием оцените своё состояние сейчас по шкале от -10 до +10.\n\n'
        'Просто ответьте одним числом, например: 3 или -2.\n'
        'После этого я сразу пришлю ваш аудиотранс.'
    )
    if channel == 'telegram':
        from keyboards.inline import kb_mood_scale
        await bot.send_message(
            uid,
            '📍 Перед прослушиванием: оцените своё состояние сейчас (−10 … +10):\n\n'
            'Нажмите оценку — и я сразу пришлю Вам аудиотранс.',
            reply_markup=kb_mood_scale(session_id, stage='pre'),
        )
        return
    plan = build_delivery_plan(int(uid), preferred_platform=channel, fallback=channel)
    sender = senders.get(channel)
    if sender is None or not plan.external_user_id:
        raise RuntimeError(f'No sender/external id for channel={channel}')
    await sender.send_text(plan.external_user_id, prompt)


async def tick(bot: Bot):
    try:
        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        senders = SenderRegistry(telegram=TelegramBotSender(bot), max=MaxBotSender(), vk=VkBotSender())
        due_candidates = await asyncio.to_thread(_collect_due_candidates, now_utc)
        for item in due_candidates:
            uid = int(item["uid"])
            slot = str(item["slot"])
            policy = item["policy"]
            assert hasattr(policy, 'timezone')
            if policy.blocked_by_quiet_hours:
                log_event(uid, 'auto_audio_quiet_hours_block', {'slot': slot, 'tz': policy.timezone, 'preferred': policy.preferred_channel, 'resolved': policy.resolved_channel, 'next_allowed_at': policy.next_allowed_at.isoformat() if policy.next_allowed_at else None})
                continue
            tz_name = policy.timezone
            idx = await asyncio.to_thread(get_index, uid, slot)
            aa = pick_for_slot(slot, idx)
            if not aa:
                continue
            local_day = now_utc.astimezone(ZoneInfo(tz_name)).date().isoformat()
            scheduled_at = for_pre_score(uid, local_day, slot)
            kind = 'work' if slot == 'morning' else 'home'
            if not await asyncio.to_thread(mark_delivery_once, uid, kind, 'pre_score', scheduled_at):
                log_event(uid, 'idempotency_skip', {'stage': 'pre_score', 'slot': slot, 'scheduled_at': scheduled_at})
                continue
            sid = await asyncio.to_thread(create_session, uid, kind=kind, source='auto', day=local_day, slot=slot, scheduled_at=scheduled_at, anchor_id=aa.anchor)
            try:
                await _send_pre_prompt(bot, uid, session_id=sid, channel=policy.resolved_channel, senders=senders)
                log_event(uid, 'auto_audio_prompted', {'slot': slot, 'anchor': aa.anchor, 'day': local_day, 'channel': policy.resolved_channel, 'preferred': policy.preferred_channel, 'tz': tz_name})
                if policy.fallback_used:
                    log_event(uid, 'auto_audio_channel_fallback', {'slot': slot, 'preferred': policy.preferred_channel, 'resolved': policy.resolved_channel, 'tz': tz_name})
            except (RuntimeError, ValueError, TypeError) as e:
                try:
                    await asyncio.to_thread(unmark_delivery, uid, kind, 'pre_score', scheduled_at)
                except sqlite3.Error:
                    logging.getLogger(__name__).debug('pre_score idempotency cleanup failed', exc_info=True)
                log_event(uid, 'auto_audio_error', {'slot': slot, 'err': str(e), 'channel': policy.resolved_channel})
    except (sqlite3.Error, RuntimeError, ValueError):
        logging.getLogger(__name__).exception('auto_audio.tick failed')
        return
