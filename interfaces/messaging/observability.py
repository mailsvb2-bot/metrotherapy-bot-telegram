from __future__ import annotations

"""Small structured observability helpers for messaging interfaces.

This module deliberately avoids external metrics dependencies. It gives the
runtime and tests a stable event naming contract today, and can later be wired
to a metrics backend/control-plane without changing adapters.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

MessagingChannel = Literal["telegram", "max", "vk", "web"]
MessagingEventStatus = Literal["ok", "error", "skipped", "rejected"]

log = logging.getLogger("interfaces.messaging")


@dataclass(frozen=True)
class MessagingObservation:
    channel: MessagingChannel
    event: str
    status: MessagingEventStatus
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return f"messaging.{self.channel}.{self.event}.{self.status}"


def emit_messaging_observation(observation: MessagingObservation) -> None:
    # Never log secrets/tokens; callers should pass only redacted metadata.
    log.info(
        observation.name,
        extra={
            "messaging_channel": observation.channel,
            "messaging_event": observation.event,
            "messaging_status": observation.status,
            "messaging_meta": observation.meta,
        },
    )


def observe(channel: MessagingChannel, event: str, status: MessagingEventStatus = "ok", **meta: Any) -> MessagingObservation:
    observation = MessagingObservation(channel=channel, event=event, status=status, meta=dict(meta))
    emit_messaging_observation(observation)
    return observation
