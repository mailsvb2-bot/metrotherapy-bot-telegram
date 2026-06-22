from __future__ import annotations

import logging
import sqlite3

from services.db import db
from services.subscription import has_access
from services.practice_tokens import enforcement_mode, get_wallet, token_economy_enabled

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
        return [int(r[0]) for r in rows]
    except sqlite3.Error:
        log.exception("subscription entitlement query failed")
        return []


def practice_wallet_user_ids() -> list[int]:
    if not token_economy_enabled():
        return []
    mode = enforcement_mode()
    if mode == "off":
        return []

    try:
        with db() as conn:
            if mode == "hard":
                rows = conn.execute(
                    """
                    SELECT user_id FROM practice_wallets
                    WHERE COALESCE(available_tokens,0) > 0
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT user_id FROM practice_wallets
                    WHERE COALESCE(available_tokens,0) > 0
                       OR COALESCE(reserved_tokens,0) > 0
                    """
                ).fetchall()
        return [int(r[0]) for r in rows]
    except sqlite3.Error:
        log.exception("practice token entitlement query failed")
        return []


def eligible_user_ids(slot: str) -> list[int]:
    return sorted(set(subscription_user_ids(slot)) | set(practice_wallet_user_ids()))


def has_entitlement(user_id: int, slot: str) -> bool:
    slot = "morning" if str(slot) == "morning" else "evening"
    try:
        if has_access(int(user_id), slot):
            return True
    except sqlite3.Error:
        log.exception("subscription entitlement check failed")
        return False
    if not token_economy_enabled() or enforcement_mode() == "off":
        return False
    try:
        wallet = get_wallet(int(user_id))
        if int(wallet.available_tokens) > 0:
            return True
        return enforcement_mode() == "soft"
    except sqlite3.Error:
        log.exception("practice token entitlement db check failed")
        return enforcement_mode() == "soft"
    except (TypeError, ValueError):
        log.exception("practice token entitlement value check failed")
        return enforcement_mode() == "soft"
