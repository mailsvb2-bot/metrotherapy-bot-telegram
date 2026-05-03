from __future__ import annotations

"""Unified conversation contracts.

These types are the channel-independent boundary between messenger transports
and the canonical Metrotherapy conversation/funnel logic. They intentionally do
not contain business decisions. Adapters map raw channel updates into
ConversationEvent; renderers map CanonicalResponse into channel-specific API
payloads.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

ConversationPlatform = Literal["telegram", "max", "vk", "web"]
ConversationEventKind = Literal["message", "button", "start", "unknown"]
CanonicalButtonKind = Literal["command", "link"]


@dataclass(frozen=True)
class ConversationUser:
    user_id: int
    external_user_id: str
    platform: ConversationPlatform
    username: str | None = None
    display_name: str | None = None
    first_name: str | None = None


@dataclass(frozen=True)
class ConversationEvent:
    platform: ConversationPlatform
    kind: ConversationEventKind
    user: ConversationUser
    text: str
    event_key: str
    raw: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalButton:
    text: str
    action: str
    kind: CanonicalButtonKind = "command"
    url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalResponse:
    text: str
    buttons: tuple[tuple[CanonicalButton, ...], ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderedMessage:
    text: str
    payload: dict[str, Any]
    meta: dict[str, Any] = field(default_factory=dict)
