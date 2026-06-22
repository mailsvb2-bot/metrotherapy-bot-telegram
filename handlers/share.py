from __future__ import annotations
import logging


from aiogram import Router, F
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramNotFound, TelegramRetryAfter, TelegramNetworkError
from aiogram.types import (
    CallbackQuery, Message,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton,
)
import asyncio

from core.callback_utils import safe_answer_callback
try:
    from aiogram.types import KeyboardButtonRequestUser
except (ImportError, AttributeError):  # pragma: no cover
    KeyboardButtonRequestUser = None  # type: ignore

from keyboards.inline import kb_main
from services.pending import set_pending, pop_pending, peek_pending
from services.events import log_event
from services.promo_texts import get_share_template
from services.messenger.links import build_share_targets
from services.bg import tm

router = Router()


def _share_kb(referrer_user_id: int, text: str, back_cb: str = 'menu:main') -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in build_share_targets(referrer_user_id, text=text):
        rows.append([InlineKeyboardButton(text=f"📨 Поделиться в {item['title']}", url=item['url'])])
    rows.append([InlineKeyboardButton(text='⬅️ Назад', callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _pick_user_keyboard() -> ReplyKeyboardMarkup:
    rows = []
    if KeyboardButtonRequestUser is not None:
        try:
            rows.append([KeyboardButton(text='👤 Выбрать друга', request_user=KeyboardButtonRequestUser(request_id=1))])
        except TypeError:
            return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='❌ Отмена')]], resize_keyboard=True, one_time_keyboard=True)
    rows.append([KeyboardButton(text='❌ Отмена')])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def _build_share_payload(cb: CallbackQuery) -> tuple[str, str, str]:
    uid = int(cb.from_user.id)
    from_name = (cb.from_user.full_name or '').strip() or 'друг'
    share_text = get_share_template().format(link='', from_name=from_name).strip()
    return '', from_name, share_text


@router.callback_query(F.data == 'share:menu')
async def share_menu(cb: CallbackQuery):
    await safe_answer_callback(cb)

    uid = int(cb.from_user.id)
    _, from_name, share_text = _build_share_payload(cb)
    # Platform share uses inline URL buttons and must not open a stale user-pick mode.
    log_event(uid, 'share_menu', {'mode': 'platform_choice'})
    await cb.message.answer(
        '📣 Куда хотите посоветовать «Метротерапию»?\n\n'
        'Выберите мессенджер — ссылка откроется именно там. Если канал не отображается, проверьте настройки TELEGRAM_BOT_USERNAME, MAX_BOT_LINK_BASE/MAX_BOT_NAME и VK_GROUP_ID.',
        reply_markup=_share_kb(uid, share_text),
    )


@router.callback_query(F.data == 'share:pick')
async def share_pick(cb: CallbackQuery):
    # Backward-compatible Telegram-only direct delivery. Kept for old callback contracts,
    # but the main UX now routes through share:menu platform choice.
    await safe_answer_callback(cb)

    uid = int(cb.from_user.id)
    bot_username = ((cb.bot.username if getattr(cb.bot, 'username', None) else None) or '').strip()
    link = f'https://t.me/{bot_username}?start=ref_{uid}' if bot_username else ''
    from_name = (cb.from_user.full_name or '').strip() or 'друг'
    share_text = get_share_template().format(link=link, from_name=from_name)
    set_pending(uid, 'share', {'link': link, 'text': share_text, 'from_name': from_name})
    log_event(uid, 'share_pick', {'mode': 'telegram_user_picker_legacy'})
    await cb.message.answer(
        'Выберите друга в Telegram, чтобы отправить ему рекомендацию.',
        reply_markup=_pick_user_keyboard(),
    )


async def _deliver_share_messages(bot, uid: int, picked_ids: list[int], final: str) -> int:
    ok = 0
    for to_id in picked_ids[:1]:
        try:
            await asyncio.wait_for(bot.send_message(int(to_id), final), timeout=2.5)
            ok += 1
        except asyncio.TimeoutError:
            logging.getLogger(__name__).warning('share: send timeout', extra={'from_id': uid, 'to_id': int(to_id)})
        except (TelegramForbiddenError, TelegramNotFound):
            logging.getLogger(__name__).debug('share: cannot deliver', extra={'from_id': uid, 'to_id': int(to_id)})
        except (TelegramRetryAfter, TelegramNetworkError) as e:
            logging.getLogger(__name__).warning('share: temporary telegram/network error', extra={'from_id': uid, 'to_id': int(to_id), 'err': str(e)})
        except TelegramAPIError:
            logging.getLogger(__name__).exception('share: telegram api error', extra={'from_id': uid, 'to_id': int(to_id)})
    return ok


async def _finalize_share_delivery(message: Message, uid: int, picked_ids: list[int], final: str, link: str) -> None:
    ok = await _deliver_share_messages(message.bot, uid, picked_ids, final)
    if ok:
        await message.answer('✅ Рекомендация отправлена.')
        log_event(uid, 'share_sent_ok', {'picked': len(picked_ids), 'sent': ok})
        return

    await message.answer(
        '⚠️ Telegram не дал отправить сообщение выбранному пользователю автоматически.\n'
        'Скорее всего, этот человек ещё не запускал бота.\n\n'
        'Отправьте ссылку вручную:\n' + (link or '(ссылка недоступна — проверьте username бота)'),
    )
    log_event(uid, 'share_sent_fail', {'picked': len(picked_ids), 'sent': ok})


@router.message(F.users_shared)
async def users_shared(message: Message):
    uid = int(message.from_user.id)
    peek = peek_pending(uid)
    if not peek or peek.kind != 'share':
        return
    p = pop_pending(uid)
    if not p:
        return

    try:
        shared = message.users_shared
        picked_ids = [int(u.user_id) for u in (shared.users or [])]
    except (AttributeError, TypeError, ValueError):
        picked_ids = []

    link = p.data.get('link') or ''
    from_name = (p.data.get('from_name') or (message.from_user.full_name or '') or 'друг').strip() or 'друг'
    txt = (p.data.get('text') or '')
    final = txt if '{link}' not in txt else txt.format(link=link, from_name=from_name)

    if not picked_ids:
        await message.answer(
            'Не удалось получить выбранного пользователя. Отправьте ссылку вручную:\n' + (link or '(ссылка недоступна — проверьте username бота)'),
            reply_markup=ReplyKeyboardRemove(),
        )
        await message.answer('Главное меню:', reply_markup=kb_main(user_id=message.from_user.id))
        log_event(uid, 'share_pick_empty', {'picked': 0})
        return

    tm().create(_finalize_share_delivery(message, uid, picked_ids[:1], final, link), name='share-delivery')
    await message.answer('✅ Друг выбран. Возвращаю в меню.', reply_markup=ReplyKeyboardRemove())
    await message.answer('Главное меню:', reply_markup=kb_main(user_id=message.from_user.id))


@router.message(F.text == '❌ Отмена')
async def cancel(message: Message):
    pop_pending(int(message.from_user.id))

    await message.answer('Ок.', reply_markup=ReplyKeyboardRemove())
    await message.answer(
        'Главное меню:',
        reply_markup=kb_main(user_id=message.from_user.id),
    )
