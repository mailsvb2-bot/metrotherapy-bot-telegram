from __future__ import annotations

import json
import time
from typing import Any

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


class VkBotSender(_VkBotSender):
    """Stable VK sender facade after provider/UI decomposition.

    Older tests and callers used two keyboard helpers as static methods on
    VkBotSender.  The canonical implementation now lives in
    runtime.messenger_vk_ui; these methods are intentionally thin proxies so the
    sender does not regain ownership of UI logic.
    """

    @staticmethod
    def _telegram_main_parity_keyboard_json(keyboard_json: str) -> str:
        return telegram_main_parity_keyboard_json(keyboard_json)

    @staticmethod
    def _prepare_vk_keyboard_json(keyboard_json: str, *, external_user_id: str, text: str) -> str:
        return prepare_vk_keyboard_json(keyboard_json, external_user_id=external_user_id, text=text)

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        random_id = kwargs.get("random_id")
        if random_id is None:
            random_id = int(time.time_ns() % 2147483647)

        message_text = str(text or "")
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
]
