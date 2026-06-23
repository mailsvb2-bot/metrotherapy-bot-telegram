from __future__ import annotations

import logging
from aiogram import Router
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from handlers.text_input_parts.common import is_superadmin
from handlers.text_input_parts.states import RolesInputState
from keyboards.inline import kb_back_main
from services.roles import ALL_ROLES, grant_role, revoke_role

router = Router()

def _message_user_id(message: Message) -> int | None:
    user = message.from_user
    return user.id if user is not None else None


@router.message(RolesInputState.grant)
async def msg_role_grant(message: Message, state: FSMContext):
    admin_id = _message_user_id(message)
    if admin_id is None or not is_superadmin(admin_id):
        await state.clear()
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2 or not parts[0].isdigit():
        return await message.answer(
            "Формат: <user_id> <role>\nРоли: admin / support / marketing",
            reply_markup=kb_back_main(),
        )
    uid = int(parts[0])
    role = parts[1].strip().lower()
    if role not in ALL_ROLES:
        return await message.answer("Неизвестная роль. Доступно: admin / support / marketing", reply_markup=kb_back_main())
    grant_role(uid, role)
    await state.clear()
    await message.answer(f"✅ Роль '{role}' выдана пользователю {uid}.", reply_markup=kb_back_main())




@router.message(RolesInputState.revoke)
async def msg_role_revoke(message: Message, state: FSMContext):
    admin_id = _message_user_id(message)
    if admin_id is None or not is_superadmin(admin_id):
        await state.clear()
        return
    parts = (message.text or "").strip().split()
    if len(parts) < 2 or not parts[0].isdigit():
        return await message.answer(
            "Формат: <user_id> <role>\nРоли: admin / support / marketing",
            reply_markup=kb_back_main(),
        )
    uid = int(parts[0])
    role = parts[1].strip().lower()
    revoke_role(uid, role)
    await state.clear()
    await message.answer(f"✅ Роль '{role}' отозвана у пользователя {uid}.", reply_markup=kb_back_main())

