import logging
import asyncio
import sqlite3
from datetime import datetime, timedelta

from aiogram.exceptions import TelegramAPIError
from core.time_utils import utc_now

from services.funnel import step_done, mark_step, first_ts_for


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


async def _funnel_reminder_once(bot) -> None:
    """Process one reminder sweep without terminating the long-lived worker.

    Database outages, malformed legacy timestamps and one user's broken state are
    isolated to the current sweep/user. The outer loop can therefore recover on
    the next ten-minute iteration instead of silently dying forever.
    """
    from services.db import db

    try:
        with db() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT user_id
                FROM events
                WHERE event='funnel:demo_listened'
                """
            ).fetchall()
    except sqlite3.Error:
        logging.getLogger(__name__).exception("Reminder user scan failed")
        return

    now = utc_now()

    for row in rows:
        try:
            user_id = int(row["user_id"])
            demo_ts = first_ts_for(user_id, "funnel:demo_listened")
            if not demo_ts:
                continue
            elapsed = now - _parse(demo_ts)
            reminded = step_done(user_id, "reminded_1")
            deadline_done = step_done(user_id, "deadline_1")
        except (sqlite3.Error, KeyError, TypeError, ValueError):
            logging.getLogger(__name__).exception("Reminder state read failed", extra={"row": repr(row)})
            continue

        if elapsed >= timedelta(hours=1) and not reminded:
            try:
                await bot.send_message(
                    user_id,
                    "🌀 Вы уже попробовали демо.\n\n"
                    "Если Вы хотите *реальный эффект*, он начинается в серии.\n"
                    "Выберите тариф и откройте доступ: /subscribe",
                    parse_mode="Markdown",
                )
                mark_step(user_id, "reminded_1")
            except (TelegramAPIError, sqlite3.Error):
                logging.getLogger(__name__).exception("Reminder send failed", extra={"user_id": user_id})
            continue

        if elapsed >= timedelta(hours=24) and not deadline_done:
            try:
                await bot.send_message(
                    user_id,
                    "⏳ *Дедлайн по входу в поток*\n\n"
                    "Если Вы хотите продолжить — зайдите сейчас.\n"
                    "Нажмите /subscribe и выберите срок.",
                    parse_mode="Markdown",
                )
                mark_step(user_id, "deadline_1")
            except (TelegramAPIError, sqlite3.Error):
                logging.getLogger(__name__).exception("Reminder send failed", extra={"user_id": user_id})


async def funnel_reminder(bot):
    while True:
        await asyncio.sleep(600)  # каждые 10 минут
        await _funnel_reminder_once(bot)
