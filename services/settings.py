from __future__ import annotations

from typing import Optional

from services.delivery_preferences import get_user_timezone


def get_user_tz(user_id: int) -> Optional[str]:
    return get_user_timezone(int(user_id))
