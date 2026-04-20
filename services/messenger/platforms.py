from __future__ import annotations

from enum import StrEnum


class MessengerPlatform(StrEnum):
    TELEGRAM = 'telegram'
    MAX = 'max'
    VK = 'vk'


_ALLOWED = {p.value for p in MessengerPlatform}
_TITLES = {
    MessengerPlatform.TELEGRAM.value: 'Telegram',
    MessengerPlatform.MAX.value: 'MAX',
    MessengerPlatform.VK.value: 'ВКонтакте',
}


def normalize_platform(value: str | None) -> str:
    raw = (value or '').strip().lower()
    return raw if raw in _ALLOWED else MessengerPlatform.TELEGRAM.value


def platform_title(value: str | None) -> str:
    return _TITLES[normalize_platform(value)]


def is_valid_platform(value: str | None) -> bool:
    return ((value or '').strip().lower()) in _ALLOWED


def parse_platform(value: str | None) -> str | None:
    raw = (value or '').strip().lower()
    return raw if raw in _ALLOWED else None
