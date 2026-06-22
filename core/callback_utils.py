from __future__ import annotations

import logging
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

log = logging.getLogger(__name__)


async def safe_answer_callback(cb: Any, *args: Any, **kwargs: Any) -> bool:
    """
    Best-effort Telegram callback acknowledgement.

    Telegram may reject answerCallbackQuery when the callback query is expired,
    duplicated, already answered, or network temporarily fails. This must not
    break the real business handler.
    """
    try:
        await cb.answer(*args, **kwargs)
        return True
    except TelegramBadRequest as exc:
        msg = str(exc)
        if (
            "query is too old" in msg
            or "response timeout expired" in msg
            or "query ID is invalid" in msg
            or "query is invalid" in msg
        ):
            log.info("Callback answer ignored", extra={"reason": msg})
            return False
        raise
    except TelegramNetworkError as exc:
        log.warning("Callback answer network failure", extra={"reason": str(exc)})
        return False
