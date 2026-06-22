from __future__ import annotations

# Compatibility facade: provider implementations live in dedicated modules.
# Keep this module as the stable import surface for existing runtime code/tests.

from runtime.messenger_max_sender import MaxBotSender
from runtime.messenger_telegram_sender import TelegramBotSender
from runtime.messenger_transport_errors import MessengerMediaNotReadyError, MessengerTransportError
from runtime.messenger_vk_sender import VkBotSender as _VkBotSender
from runtime.messenger_vk_ui import prepare_vk_keyboard_json, telegram_main_parity_keyboard_json


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


__all__ = [
    "MessengerTransportError",
    "MessengerMediaNotReadyError",
    "TelegramBotSender",
    "MaxBotSender",
    "VkBotSender",
]
