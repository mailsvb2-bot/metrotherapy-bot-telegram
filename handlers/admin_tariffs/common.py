from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional

from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from core.callbacks import ADMIN_TARIFFS
from handlers.admin_inline_common import safe_edit
from handlers.admin_inline_states import AdminManageState
from services.db import get_connection
from services.plans import get_active_plans, get_plans

log = logging.getLogger(__name__)


def _pricing():
    # Lazy import to keep interface layer free from direct economy module imports (Decision Sovereignty).
    from services import pricing as _p
    return _p


@dataclass(frozen=True)
class TariffsCtx:
    is_superadmin: bool
    can_manage_tariffs: bool
    staff_kb: InlineKeyboardMarkup


def parse_price_int(text: str) -> Optional[int]:
    """Parse an integer price from admin input.

    Accepts:
    - "990", "990р", "990 ₽"
    - "1 990", "1,990", "1.990"
    """
    if text is None:
        return None
    s = str(text).strip().lower()
    if not s:
        return None
    for ch in ["₽", "р", "руб", "руб."]:
        s = s.replace(ch, "")
    s = s.replace(" ", "").replace("\u00a0", "")
    s = s.replace(",", "").replace(".", "")
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return None
    try:
        value = int(digits)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value




