import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramAPIError


from services.admin import is_admin
from services.demo_analytics import demo_summary
from services.events import funnel_counts
from services.store import store

router = Router()


def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user is not None else None


def _fmt_sec(x: int | float | None) -> str:
    if x is None:
        return "-"
    try:
        x_int = int(x)
    except (TypeError, ValueError):
        return "-"
    m = x_int // 60
    s = x_int % 60
    return f"{m:02d}:{s:02d}"


async def _deny(message: Message) -> None:
    try:
        await message.answer("Недоступно.")
    except TelegramAPIError:
        logging.getLogger(__name__).exception("Unhandled exception")


def _uid(message: Message) -> int | None:
    try:
        return _message_user_id(message)
    except AttributeError:
        return None


@router.message(Command("admin"))
async def admin_cmd(message: Message):
    if not is_admin(_uid(message)):
        return await _deny(message)

    await message.answer(
        "🛠 Админ\n\n"
        "Доступные команды:\n"
        "• /demo_stats — демо-аналитика\n"
        "• /funnel — воронка (уникальные пользователи)\n"
        "• /retention — базовая статистика удержания\n"
        "\nПодсказка: удобнее пользоваться кнопкой \"🛠 Панель\" в меню."
    )


@router.message(Command("demo_stats"))
async def demo_stats_cmd(message: Message):
    if not is_admin(_uid(message)):
        return await _deny(message)

    s = demo_summary()
    await message.answer(
        "📊 Демо-аналитика\n\n"
        f"Отправлено «на работу»: {s['sent_work']}\n"
        f"Отправлено «домой»: {s['sent_home']}\n"
        "\n"
        f"Отметили прослушивание (work): {s['ack_work']}\n"
        f"Отметили прослушивание (home): {s['ack_home']}\n"
        f"Прослушали оба (по отметкам): {s['both_acked_users']}\n"
        "\n"
        f"Среднее время до отметки: {_fmt_sec(s['avg_ack_delay_sec'])}\n"
        "\n"
        "ℹ️ Telegram не сообщает реальное время прослушивания."
    )


@router.message(Command("funnel"))
async def funnel_cmd(message: Message):
    if not is_admin(_uid(message)):
        return await _deny(message)

    evs = [
        "view_tariffs",
        "invoice_created",
        "invoice_paid",
        "sub_paid",
        "demo_scheduled",
        "demo_sent",
        "demo_ack",
    ]
    c = funnel_counts(evs)

    await message.answer(
        "📉 Воронка (уникальные пользователи)\n\n"
        f"Тарифы: просмотрели — {c['view_tariffs']}\n"
        f"Счёт: создан — {c['invoice_created']}\n"
        f"Счёт: оплачен — {c['invoice_paid']}\n"
        f"Подписка: оплачена — {c['sub_paid']}\n"
        "\n"
        f"Демо: запланировали — {c['demo_scheduled']}\n"
        f"Демо: отправлено — {c['demo_sent']}\n"
        f"Демо: отметили прослушивание — {c['demo_ack']}\n"
    )


@router.message(Command("retention"))
async def retention_cmd(message: Message):
    if not is_admin(_uid(message)):
        return await _deny(message)

    missing = store.users_missing_times()
    users = store.count_users()

    await message.answer(
        "🧩 Удержание (базово)\n\n"
        f"Пользователей всего: {users}\n"
        f"Не назначили время «на работу»: {missing['missing_work']}\n"
        f"Не назначили время «домой»: {missing['missing_home']}\n\n"
        "Идея следующего шага: отчёт по «пропустил N дней» на базе events audio_sent."
    )
