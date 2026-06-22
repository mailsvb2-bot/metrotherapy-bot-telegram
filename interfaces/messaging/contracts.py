from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MessengerPlatform = Literal["telegram", "vk", "max"]
ButtonKind = Literal["callback", "url"]


def normalize_platform(raw: str) -> MessengerPlatform:
    value = (raw or "").strip().lower()
    if value in {"telegram", "tg"}:
        return "telegram"
    if value in {"vk", "vkontakte", "vk.com"}:
        return "vk"
    if value in {"max", "max.ru"}:
        return "max"
    raise ValueError(f"Unsupported messenger platform: {raw!r}")


@dataclass(frozen=True)
class MessengerButton:
    label: str
    kind: ButtonKind
    payload: str

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("Messenger button label is required")
        if self.kind not in {"callback", "url"}:
            raise ValueError(f"Unsupported messenger button kind: {self.kind!r}")
        if not self.payload.strip():
            raise ValueError("Messenger button payload is required")
        if self.kind == "url" and not self.payload.startswith(("https://", "http://")):
            raise ValueError("URL button payload must be an absolute URL")


@dataclass(frozen=True)
class MessengerMessage:
    platform: MessengerPlatform
    external_user_id: str
    text: str
    buttons: tuple[MessengerButton, ...] = ()
    media: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = normalize_platform(self.platform)
        object.__setattr__(self, "platform", normalized)
        if not str(self.external_user_id).strip():
            raise ValueError("external_user_id is required")
        if not self.text.strip() and not self.media:
            raise ValueError("Messenger message requires text or media")
        object.__setattr__(self, "buttons", tuple(self.buttons))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def has_buttons(self) -> bool:
        return bool(self.buttons)
