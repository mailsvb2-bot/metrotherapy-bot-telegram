import logging
import sqlite3
from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command

from services.messenger.entrypoints import register_user_entry
from services.events import log_event

from handlers.menu import send_main_menu
from keyboards.inline import kb_start_landing, kb_demo_kind

router = Router()

START_LANDING_TEXT = (
    "🌿 Добро пожаловать в Метротерапию\n\n"
    "Это короткие аудиопрактики для спокойствия, сна и восстановления.\n\n"
    "Начните с бесплатной практики: за несколько минут бот поможет выбрать мягкий маршрут "
    "и сохранить прогресс, чтобы Вы могли продолжить с нужного места.\n\n"
    "Важно: Метротерапия не заменяет врача, психотерапевта или экстренную помощь."
)

HELP_TEXT = (
    "❓ Помощь\n\n"
    "Главный путь: нажмите «Начать бесплатную практику», выберите демо и следуйте подсказкам.\n\n"
    "Если что-то зависло: отправьте /start ещё раз.\n"
    "Если состояние острое или небезопасное — обратитесь за живой профессиональной помощью."
)

PRIVACY_TEXT = (
    "🔒 Конфиденциальность\n\n"
    "Бот сохраняет технический прогресс прохождения практик и события воронки, "
    "чтобы корректно выдавать доступ, напоминания и аналитику.\n\n"
    "Не отправляйте в бот экстренные медицинские данные. При остром состоянии обращайтесь к специалисту."
)

SITE_TEXT = "🌐 Сайт проекта: https://metrotherapy.ru"

def _log_safe(user_id: int, event: str, payload: dict | None = None) -> None:
    try:
        log_event(int(user_id), event, payload or {})
    except Exception:
        logging.getLogger(__name__).debug("funnel event skipped", exc_info=True)




@router.message(CommandStart())
async def start_cmd(message: Message):
    payload = ""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        payload = parts[1].strip()

    try:
        register_user_entry(
            message.from_user.id,
            platform='telegram',
            external_user_id=str(message.from_user.id),
            username=message.from_user.username,
            display_name=message.from_user.full_name,
            first_name=message.from_user.first_name,
            start_payload=payload,
        )
    except ValueError:
        logging.getLogger(__name__).exception("Bad start payload", extra={"payload": payload, "user_id": message.from_user.id})
        pass
    if payload.startswith("gift_"):
        code = payload.replace("gift_", "").strip()
        # Variant A: не активируем автоматически. Сначала — принятие подарка.
        try:
            from handlers.gift_flow import send_gift_intro
            await send_gift_intro(message, code)
        except ImportError:
            logging.getLogger(__name__).exception("gift_flow import failed")
            await message.answer("🎁 Вам подарили «Метротерапию». Откройте ссылку ещё раз.")
        except (sqlite3.Error, TelegramAPIError):
            logging.getLogger(__name__).exception("gift intro failed", extra={"code": code, "user_id": message.from_user.id})
            await message.answer("🎁 Вам подарили «Метротерапию». Откройте ссылку ещё раз.")
        except (ValueError, TypeError, AttributeError):
            logging.getLogger(__name__).exception("gift intro failed (unexpected)", extra={"code": code, "user_id": message.from_user.id})
            await message.answer("🎁 Вам подарили «Метротерапию». Откройте ссылку ещё раз.")
        await send_main_menu(message)
        return

    _log_safe(message.from_user.id, "funnel_start_landing_seen", {"payload": payload})
    await message.answer(START_LANDING_TEXT, reply_markup=kb_start_landing(user_id=message.from_user.id))


@router.message(Command("programs"))
async def programs_cmd(message: Message):
    _log_safe(message.from_user.id, "funnel_programs_command", {})
    await message.answer(
        "🎧 Выберите бесплатную практику. После неё можно будет открыть полный маршрут.",
        reply_markup=kb_demo_kind(),
    )


@router.message(Command("tariffs"))
async def tariffs_cmd(message: Message):
    _log_safe(message.from_user.id, "funnel_tariffs_command", {})
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Открыть тарифы", callback_data="sub:menu")],
            [InlineKeyboardButton(text="🟢 Сначала бесплатная практика", callback_data="demo")],
        ]
    )
    await message.answer(
        "💳 Тарифы\n\n"
        "Лучше начать с бесплатной практики, а затем открыть полный маршрут. "
        "Если уже готовы — нажмите «Открыть тарифы».",
        reply_markup=kb,
    )


@router.message(Command("progress"))
async def progress_cmd(message: Message):
    _log_safe(message.from_user.id, "funnel_progress_command", {})
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📈 Открыть анализ", callback_data="settings:state")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
        ]
    )
    await message.answer("📍 Прогресс и состояние доступны в разделе анализа.", reply_markup=kb)


@router.message(Command("help"))
async def help_cmd(message: Message):
    _log_safe(message.from_user.id, "funnel_help_command", {})
    await message.answer(HELP_TEXT, reply_markup=kb_start_landing(user_id=message.from_user.id))


@router.message(Command("site"))
async def site_cmd(message: Message):
    _log_safe(message.from_user.id, "funnel_site_command", {})
    await message.answer(SITE_TEXT)


@router.message(Command("privacy"))
async def privacy_cmd(message: Message):
    _log_safe(message.from_user.id, "funnel_privacy_command", {})
    await message.answer(PRIVACY_TEXT)
