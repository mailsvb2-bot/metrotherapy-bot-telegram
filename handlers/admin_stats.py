import asyncio
import logging

log = logging.getLogger(__name__)
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from services.db import db
from services.state_log import fetch_last
from services.admin import is_admin

router = Router()


def _stats_snapshot() -> tuple[int, int, int]:
    with db() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        subs = conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
    return int(users), int(events), int(subs)


def _fetch_state_last(uid: int, *, limit: int):
    return fetch_last(int(uid), limit=int(limit))


@router.message(Command("stats"))
async def stats(message: Message):
    uid = message.from_user.id if message.from_user else None
    if not is_admin(uid):
        return

    users, events, subs = await asyncio.to_thread(_stats_snapshot)

    await message.answer(
        "📊 Статистика\n\n"
        f"👤 Пользователи: {users}\n"
        f"🔐 Подписок: {subs}\n"
        f"⚡ Событий: {events}"
    )


@router.message(Command("state_last"))
async def state_last(message: Message):
    """Показывает последние записи user_state_log.

    Использование:
      /state_last               -> по себе (админ)
      /state_last <user_id>     -> по пользователю
      /state_last <user_id> <n> -> лимит (1..50)
    """

    uid0 = message.from_user.id if message.from_user else None
    if not is_admin(uid0):
        return

    parts = (message.text or "").split()
    uid = message.from_user.id
    limit = 10
    if len(parts) >= 2 and parts[1].isdigit():
        uid = int(parts[1])
    if len(parts) >= 3 and parts[2].isdigit():
        limit = int(parts[2])

    items = await asyncio.to_thread(_fetch_state_last, uid, limit=limit)
    if not items:
        return await message.answer(f"🧾 state-log: записей нет (user_id={uid})")

    lines: list[str] = [f"🧾 state-log (user_id={uid}, последние {len(items)}):"]
    for i, it in enumerate(items, start=1):
        ts = str(it.get("ts") or "")
        st = str(it.get("state") or "")
        meta = it.get("meta")
        meta_s = ""
        if meta is not None:
            try:
                # meta может быть dict/str
                meta_s = str(meta)
            except (TypeError, ValueError):
                meta_s = ""
        if meta_s:
            if len(meta_s) > 180:
                meta_s = meta_s[:180] + "…"
            lines.append(f"{i}) {ts} | {st} | {meta_s}")
        else:
            lines.append(f"{i}) {ts} | {st}")

    # Телеграм режет слишком длинные сообщения — подстрахуемся
    text = "\n".join(lines)
    if len(text) > 3500:
        text = text[:3500] + "\n…"
    await message.answer(text)
