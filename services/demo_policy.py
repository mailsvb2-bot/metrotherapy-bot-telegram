"""Canonical try-before-buy / demo access policy.

Regular users may receive at most two free demo tracks: work + home.
Admins/test operators may repeat demos indefinitely for production checks.

This module is the single policy surface for free demo/trial access.  It does
not send messages, create jobs, grant subscriptions, or decide marketing copy;
it only answers policy questions so Telegram handlers, engine jobs, analytics,
and admin reports do not grow a second trial brain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from services.admin import is_admin

DEMO_KINDS: tuple[str, str] = ("work", "home")
MAX_REGULAR_DEMOS = len(DEMO_KINDS)


@dataclass(frozen=True)
class TrialPolicyStatus:
    """Canonical free-trial state derived from already persisted demo events."""

    user_id: int
    sent_kinds: tuple[str, ...]
    remaining_kinds: tuple[str, ...]
    admin_bypass: bool

    @property
    def sent_count(self) -> int:
        return len(self.sent_kinds)

    @property
    def remaining_count(self) -> int:
        return len(self.remaining_kinds)

    @property
    def exhausted(self) -> bool:
        return not self.admin_bypass and self.remaining_count <= 0

    @property
    def stage(self) -> str:
        if self.admin_bypass:
            return "admin_bypass"
        if self.sent_count <= 0:
            return "not_started"
        if self.exhausted:
            return "completed_both"
        return "started"


def _normalize_kind(kind: str | None) -> str | None:
    value = (kind or "").strip().lower()
    return value if value in DEMO_KINDS else None


def normalize_sent_kinds(sent_kinds: Iterable[str] | None) -> tuple[str, ...]:
    """Return deterministic canonical sent kinds without unknown values."""

    sent = {_normalize_kind(k) for k in (sent_kinds or [])}
    return tuple(k for k in DEMO_KINDS if k in sent)


def remaining_demo_kinds(sent_kinds: Iterable[str] | None) -> tuple[str, ...]:
    sent = set(normalize_sent_kinds(sent_kinds))
    return tuple(k for k in DEMO_KINDS if k not in sent)


def trial_status_for_user(user_id: int, sent_kinds: Iterable[str] | None) -> TrialPolicyStatus:
    """Build the canonical free-trial status from persisted demo history.

    The caller passes sent_kinds to keep this module free from storage concerns.
    Storage remains owned by services.demo_analytics / services.db.
    """

    uid = int(user_id)
    sent = normalize_sent_kinds(sent_kinds)
    admin_bypass = can_repeat_demo_for_user(uid)
    remaining = DEMO_KINDS if admin_bypass else remaining_demo_kinds(sent)
    return TrialPolicyStatus(
        user_id=uid,
        sent_kinds=sent,
        remaining_kinds=remaining,
        admin_bypass=admin_bypass,
    )


def can_receive_demo_kind(user_id: int, kind: str, sent_kinds: Iterable[str] | None) -> bool:
    """Return whether the user may receive the requested free demo kind."""

    normalized = _normalize_kind(kind)
    if normalized is None:
        return False
    status = trial_status_for_user(int(user_id), sent_kinds)
    if status.admin_bypass:
        return True
    return normalized in status.remaining_kinds


def next_remaining_demo_kind(kind: str | None, sent_kinds: Iterable[str] | None) -> str | None:
    """Prefer the opposite demo kind, then any remaining kind."""

    sent = set(normalize_sent_kinds(sent_kinds))
    current = _normalize_kind(kind)
    if current == "work" and "home" not in sent:
        return "home"
    if current == "home" and "work" not in sent:
        return "work"
    for candidate in DEMO_KINDS:
        if candidate not in sent:
            return candidate
    return None


def can_repeat_demo_for_user(user_id: int) -> bool:
    """Return True when demo replay limits should be bypassed for this user."""
    return is_admin(int(user_id))
