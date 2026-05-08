"""Canonical demo access policy.

Regular users may receive at most two free demo tracks: work + home.
Admins/test operators may repeat demos indefinitely for production checks.
"""

from __future__ import annotations

from services.admin import is_admin


def can_repeat_demo_for_user(user_id: int) -> bool:
    """Return True when demo replay limits should be bypassed for this user."""
    return is_admin(int(user_id))
