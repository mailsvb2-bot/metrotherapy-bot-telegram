import logging
import asyncio
from datetime import datetime, timedelta

from aiogram.exceptions import TelegramAPIError
from core.time_utils import utc_now

from services.funnel import step_done, mark_step, first_ts_for

def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)

async def funnel_reminder(bot):
    while True:
        await asyncio.sleep(600)  # каждые 10 минут

        # берём пользователей, у кого был demo_listened
        # (не через SQL join здесь — чтобы код был лёгкий и надёжный)
        # используем first_ts_for() для тайминга
        # список юзеров получаем через простую выборку событий
        from services.db import db
        with db() as conn:
            rows = conn.execute("""
                SELECT DISTINCT user_id
                FROM events
                WHERE event='funnel:demo_listened'
            """).fetchall()

        now = utc_now()

        for r in rows:
            user_id = int(r["user_id"])
            demo_ts = first_ts_for(user_id, "funnel:demo_listened")
            if not demo_ts:
                continue

            elapsed = now - _parse(demo_ts)

            if elapsed >= timedelta(hours=1) and not step_done(user_id, "reminded_1"):
                try:
                    await bot.send_message(
                        user_id,
                        "🌀 Вы уже попробовали демо.\n\n"
                        "Если Вы хотите *реальный эффект*, он начинается в серии.\n"
                        "Выберите тариф и откройте доступ: /subscribe",
                        parse_mode="Markdown"
                    )
                    mark_step(user_id, "reminded_1")
                except TelegramAPIError:
                    logging.getLogger(__name__).exception("Reminder send failed", extra={"user_id": user_id})
                continue

            if elapsed >= timedelta(hours=24) and not step_done(user_id, "deadline_1"):
                try:
                    await bot.send_message(
                        user_id,
                        "⏳ *Дедлайн по входу в поток*\n\n"
                        "Если Вы хотите продолжить — зайдите сейчас.\n"
                        "Нажмите /subscribe и выберите срок.",
                        parse_mode="Markdown"
                    )
                    mark_step(user_id, "deadline_1")
                except TelegramAPIError:
                    logging.getLogger(__name__).exception("Reminder send failed", extra={"user_id": user_id})
