from __future__ import annotations

import logging
import sqlite3

from services.db import db
from services.practice_tokens import enforcement_mode, token_economy_enabled
from services.subscription import has_access

log = logging.getLogger(__name__)


def subscription_user_ids(slot: str) -> list[int]:
    if slot not in {"morning", "evening"}:
        return []
    try:
        with db() as conn:
            if slot == "morning":
                rows = conn.execute(
                    """
                    SELECT user_id FROM subscriptions
                    WHERE COALESCE(status,'active')='active'
                      AND COALESCE(total_morning,0) > 0
                      AND COALESCE(used_morning,0) < COALESCE(total_morning,0)
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT user_id FROM subscriptions
                    WHERE COALESCE(status,'active')='active'
                      AND COALESCE(total_evening,0) > 0
                      AND COALESCE(used_evening,0) < COALESCE(total_evening,0)
                    """
                ).fetchall()
        return [int(row[0]) for row in rows]
    except sqlite3.Error:
        log.exception("subscription entitlement query failed")
        return []


def practice_wallet_user_ids() -> list[int]:
    if not token_economy_enabled() or enforcement_mode() == "off":
        return []
    try:
        with db() as conn:
            rows = conn.execute(
                """
                SELECT user_id FROM practice_wallets
                WHERE COALESCE(available_tokens,0) > 0
                """
            ).fetchall()
        return [int(row[0]) for row in rows]
    except sqlite3.Error:
        log.exception("practice token entitlement query failed")
        return []


def eligible_user_ids(slot: str) -> list[int]:
    """Bulk source of truth for automatic PRE prompts."""

    if token_economy_enabled() and enforcement_mode() != "off":
        return sorted(set(practice_wallet_user_ids()))
    return sorted(set(subscription_user_ids(slot)))


def has_entitlement(user_id: int, slot: str) -> bool:
    normalized_slot = "morning" if str(slot) == "morning" else "evening"
    try:
        return bool(has_access(int(user_id), normalized_slot))
    except sqlite3.Error:
        log.exception("paid entitlement check failed")
        return enforcement_mode() == "soft"
    except TypeError:
        log.exception("paid entitlement value check failed")
        return enforcement_mode() == "soft"
    except ValueError:
        log.exception("paid entitlement value check failed")
        return enforcement_mode() == "soft"
