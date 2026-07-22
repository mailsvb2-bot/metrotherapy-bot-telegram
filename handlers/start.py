import asyncio
import hashlib
import logging
import sqlite3

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User

from handlers.menu import send_main_menu
from keyboards.inline import kb_demo_kind, kb_main
from services.acquisition_attribution import start_attribution_meta
from services.events import log_event
from services.gift_claims import claim_gift_token, is_gift_token, normalize_gift_token
from services.messenger.entrypoints import register_user_entry

router = Router()
log = logging.getLogger(__name__)

HELP_TEXT = (
    "❓ Помощь\n\n"
    "Главный путь: откройте /start, выберите демо и следуйте подсказкам.\n\n"
    "Если что-то зависло: отправьте /start ещё раз.\n"
    "Если состояние острое или небезопасное — обратитесь за живой профессиональной помощью."
)

PRIVACY_TEXT = (
    "🔒 Конфиденциальность\n\n"
    "Бот хранит данные, необходимые для работы сервиса: прогресс практик, оценки состояния, "
    "ответы о телесных ощущениях, настройки и события доставки. Платёжные и возвратные записи "
    "сохраняются отдельно для исполнения покупок и обязательного учёта.\n\n"
    "Получить копию данных: /mydata\n"
    "Удалить поведенческую историю: /deletemydata\n\n"
    "Не отправляйте в бот экстренные медицинские данные. При остром состоянии обращайтесь к специалисту."
)

SITE_TEXT = "🌐 Сайт проекта: https://metrotherapy.ru"

START_FALLBACK_TEXT = (
    "🌿 Добро пожаловать в Метротерапию.\n\n"
    "Выберите действие в меню ниже. Если Вы впервые здесь — начните с бесплатной практики."
)


def _message_user(message: Message) -> User | None:
    return message.from_user


def _user_id(message: Message) -> int | None:
    user = _message_user(message)
    return user.id if user is not None else None


def _payload_log_meta(payload: str | None) -> dict[str, object]:
    """Describe a start payload without exposing bearer tokens or raw campaign text."""
    raw = str(payload or "").strip()
    normalized = normalize_gift_token(raw)
    if is_gift_token(normalized):
        kind = "paid_gift"
    elif raw.startswith("gift_"):
        kind = "legacy_gift"
    elif raw.startswith("ad_"):
        kind = "ad_link"
    elif raw:
        kind = "attribution"
    else:
        kind = "empty"
    return {
        "payload_kind": kind,
        "payload_len": len(raw),
        "payload_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16] if raw else "",
    }


def _log_safe(user_id: int | None, event: str, payload: dict | None = None) -> None:
    if user_id is None:
        return
    try:
        log_event(int(user_id), event, payload or {})
    except (sqlite3.Error, RuntimeError, OSError, TypeError, ValueError):
        log.debug("funnel event skipped", exc_info=True)


def _register_user_entry_safe(message: Message, payload: str) -> None:
    user = _message_user(message)
    if user is None:
        return
    user_id = user.id
    safe_extra = {"user_id": user_id, **_payload_log_meta(payload)}
    try:
        register_user_entry(
            user_id,
            platform="telegram",
            external_user_id=str(user_id),
            username=user.username,
            display_name=user.full_name,
            first_name=user.first_name,
            start_payload=payload,
        )
    except ValueError:
        log.exception("Bad start payload", extra=safe_extra)
    except (sqlite3.Error, RuntimeError, OSError, TypeError, AttributeError):
        log.exception("Failed to register start entry", extra=safe_extra)


def _claim_gift_safe(message: Message, token: str) -> str:
    user_id = _user_id(message)
    if user_id is None:
        return "Подарок можно активировать только из личного профиля пользователя."
    _register_user_entry_safe(message, token)
    result = claim_gift_token(gift_token=token, recipient_user_id=int(user_id), platform="telegram")
    try:
        log_event(int(user_id), "gift_claim_attempt", {"status": result.status, "package_id": result.package_id})
    except (sqlite3.Error, RuntimeError, OSError, TypeError, ValueError):
        log.debug("gift claim event skipped", exc_info=True)
    return result.message


async def _open_main_menu_fail_open(message: Message, *, fallback_text: str = START_FALLBACK_TEXT) -> None:
    """Open the Telegram entry menu even if personalization/analytics are broken.

    /start is the public ingress of the bot. It must not be blocked by DB writes,
    personalization reads, funnel logging, or any other non-critical side effect.
    """
    try:
        await send_main_menu(message)
        return
    except (TelegramAPIError, sqlite3.Error, RuntimeError, OSError, TypeError, ValueError, AttributeError):
        log.exception(
            "Primary /start menu failed; sending minimal fail-open menu",
            extra={"user_id": _user_id(message)},
        )

    try:
        await message.answer(fallback_text, reply_markup=kb_main(user_id=_user_id(message)))
    except TelegramAPIError:
        log.exception("Fallback /start answer failed", extra={"user_id": _user_id(message)})
        raise


@router.message(CommandStart())
async def start_cmd(message: Message):
    payload = ""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        payload = parts[1].strip()

    token = normalize_gift_token(payload)
    if is_gift_token(token):
        text = await asyncio.to_thread(_claim_gift_safe, message, token)
        await message.answer(text, reply_markup=kb_main(user_id=_user_id(message)))
        await _open_main_menu_fail_open(message)
        return

    # Legacy short gift/referral payloads still need entry registration before special handling.
    if payload.startswith("gift_"):
        await asyncio.to_thread(_register_user_entry_safe, message, payload)
        code = payload.removeprefix("gift_").strip()
        safe_extra = {"user_id": _user_id(message), **_payload_log_meta(payload)}
        try:
            from handlers.gift_flow import send_gift_intro

            await send_gift_intro(message, code)
        except ImportError:
            log.exception("gift_flow import failed", extra=safe_extra)
            await message.answer("🎁 Вам подарили «Метротерапию». Откройте ссылку ещё раз.")
        except (sqlite3.Error, TelegramAPIError):
            log.exception("gift intro failed", extra=safe_extra)
            await message.answer("🎁 Вам подарили «Метротерапию». Откройте ссылку ещё раз.")
        except (ValueError, TypeError, AttributeError):
            log.exception("gift intro failed (unexpected)", extra=safe_extra)
            await message.answer("🎁 Вам подарили «Метротерапию». Откройте ссылку ещё раз.")
        await _open_main_menu_fail_open(message)
        return

    # Plain /start is a hot path: answer first, all side effects afterwards.
    await _open_main_menu_fail_open(message)
    await asyncio.to_thread(_register_user_entry_safe, message, payload)
    await asyncio.to_thread(_log_safe, _user_id(message), "funnel_start_command", start_attribution_meta(payload))


@router.message(lambda message: is_gift_token(normalize_gift_token(getattr(message, "text", ""))))
async def claim_gift_text(message: Message):
    token = normalize_gift_token(message.text or "")
    text = await asyncio.to_thread(_claim_gift_safe, message, token)
    await message.answer(text, reply_markup=kb_main(user_id=_user_id(message)))


@router.message(F.text.casefold().in_({"start", "/start", "старт", "начать", "начать заново", "меню", "menu"}))
async def safe_start_text_fallback(message: Message):
    await _open_main_menu_fail_open(message, fallback_text="🌿 Главное меню Метротерапии.\n\nВыберите действие:")


@router.message(Command("programs"))
async def programs_cmd(message: Message):
    await asyncio.to_thread(_log_safe, _user_id(message), "funnel_programs_command", {})
    await message.answer(
        "🎧 Выберите бесплатную практику. После неё можно будет открыть полный маршрут.",
        reply_markup=kb_demo_kind(),
    )


@router.message(Command("tariffs"))
async def tariffs_cmd(message: Message):
    await asyncio.to_thread(_log_safe, _user_id(message), "funnel_tariffs_command", {})
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
    await asyncio.to_thread(_log_safe, _user_id(message), "funnel_progress_command", {})
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📈 Открыть анализ", callback_data="settings:state")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="menu:main")],
        ]
    )
    await message.answer("📍 Прогресс и состояние доступны в разделе анализа.", reply_markup=kb)


@router.message(Command("help"))
async def help_cmd(message: Message):
    await asyncio.to_thread(_log_safe, _user_id(message), "funnel_help_command", {})
    await message.answer(HELP_TEXT, reply_markup=kb_main(user_id=_user_id(message)))


@router.message(Command("site"))
async def site_cmd(message: Message):
    await asyncio.to_thread(_log_safe, _user_id(message), "funnel_site_command", {})
    await message.answer(SITE_TEXT)


@router.message(Command("privacy"))
async def privacy_cmd(message: Message):
    await asyncio.to_thread(_log_safe, _user_id(message), "funnel_privacy_command", {})
    await message.answer(PRIVACY_TEXT)
