from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

# Compatibility facade: provider implementations live in dedicated modules.
# Keep this module as the stable import surface for existing runtime code/tests.

from runtime.messenger_max_sender import MaxBotSender
from runtime.messenger_telegram_sender import TelegramBotSender
from runtime.messenger_transport_errors import MessengerMediaNotReadyError, MessengerTransportError
from runtime.messenger_vk_sender import (
    VkBotSender as _VkBotSender,
    _callback_keyboard_json,
    _strip_raw_vk_payment_links,
)
from runtime.messenger_vk_ui import prepare_vk_keyboard_json, telegram_main_parity_keyboard_json

_delivery_scope: ContextVar[tuple[str, int]] = ContextVar("messenger_provider_delivery_scope", default=("", 0))


@contextmanager
def provider_delivery_scope(delivery_key: str) -> Iterator[None]:
    """Give provider sends a stable identity for one durable outbox attempt.

    VK deduplicates `messages.send` by `random_id`. The same outbox event and the
    same ordinal send therefore receive the same provider identity after a crash,
    while multiple messages inside one bundle still receive distinct identities.
    ContextVars keep concurrent deliveries isolated without global mutable maps.
    """

    token = _delivery_scope.set((str(delivery_key or "").strip(), 0))
    try:
        yield
    finally:
        _delivery_scope.reset(token)


def _next_vk_random_id() -> int:
    delivery_key, ordinal = _delivery_scope.get()
    if not delivery_key:
        return int(time.time_ns() % 2147483647) or 1
    ordinal += 1
    _delivery_scope.set((delivery_key, ordinal))
    digest = hashlib.blake2s(f"{delivery_key}:{ordinal}".encode("utf-8"), digest_size=4).digest()
    value = int.from_bytes(digest, "big") & 0x7FFFFFFF
    return value or 1


def _strip_one_time_from_vk_inline_keyboard(keyboard_json: str) -> str:
    try:
        keyboard = json.loads(str(keyboard_json or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return keyboard_json
    if not isinstance(keyboard, dict) or keyboard.get("inline") is not True:
        return keyboard_json
    if "one_time" not in keyboard:
        return keyboard_json
    normalized = dict(keyboard)
    normalized.pop("one_time", None)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _vk_provider_keyboard_json(keyboard_json: str, *, external_user_id: str, text: str) -> str:
    prepared = prepare_vk_keyboard_json(
        str(keyboard_json or ""),
        external_user_id=str(external_user_id),
        text=str(text or ""),
    )
    return _strip_one_time_from_vk_inline_keyboard(_callback_keyboard_json(prepared))


def _clean_vk_outgoing_text(text: str) -> str:
    raw = str(text or "")
    if "Главное меню" not in raw or "Кнопки ВКонтакте соответствуют" not in raw:
        return raw
    preface, _, _ = raw.partition("Главное меню")
    cleaned = f"{preface}Главное меню\n\nВыберите действие кнопками ниже."
    return cleaned.strip()


class VkBotSender(_VkBotSender):
    """Stable VK sender facade after provider/UI decomposition."""

    @staticmethod
    def _telegram_main_parity_keyboard_json(keyboard_json: str) -> str:
        return telegram_main_parity_keyboard_json(keyboard_json)

    @staticmethod
    def _prepare_vk_keyboard_json(keyboard_json: str, *, external_user_id: str, text: str) -> str:
        return prepare_vk_keyboard_json(keyboard_json, external_user_id=external_user_id, text=text)

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        random_id = kwargs.get("random_id")
        if random_id is None:
            random_id = _next_vk_random_id()

        message_text = _clean_vk_outgoing_text(str(text or ""))
        params = {"user_id": str(external_user_id), "random_id": int(random_id), "message": message_text}

        if kwargs.get("keyboard_json"):
            params["keyboard"] = _vk_provider_keyboard_json(
                str(kwargs["keyboard_json"]),
                external_user_id=str(external_user_id),
                text=message_text,
            )
            message_text = _strip_raw_vk_payment_links(message_text)
            params["message"] = message_text

        if kwargs.get("attachment"):
            params["attachment"] = kwargs["attachment"]
        data = await self._vk_method("messages.send", params)
        return data.get("response", data)


__all__ = [
    "MessengerTransportError",
    "MessengerMediaNotReadyError",
    "TelegramBotSender",
    "MaxBotSender",
    "VkBotSender",
    "provider_delivery_scope",
]
