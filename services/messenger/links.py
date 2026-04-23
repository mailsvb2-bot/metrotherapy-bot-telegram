from __future__ import annotations

import urllib.parse

from config.settings import settings

from services.messenger.platforms import MessengerPlatform


def _strip(v: str | None) -> str:
    return (v or '').strip()


def build_referral_payload(referrer_user_id: int) -> str:
    return f'ref_{int(referrer_user_id)}'


def build_bridge_payload(token: str) -> str:
    token_clean = (token or "").strip()
    return f"bridge_{token_clean}"


def _telegram_link(payload: str) -> str:
    username = _strip(settings.TELEGRAM_BOT_USERNAME)
    return f'https://t.me/{username}?start={urllib.parse.quote(payload)}' if username else ''


def _max_link(payload: str) -> str:
    bot_name = _strip(settings.MAX_BOT_NAME)
    base = _strip(settings.MAX_BOT_LINK_BASE)
    if not base:
        return ''
    if '{payload}' in base:
        return base.format(payload=urllib.parse.quote(payload), bot=urllib.parse.quote(bot_name))
    sep = '&' if '?' in base else '?'
    return f'{base}{sep}start={urllib.parse.quote(payload)}'


def _vk_link(payload: str) -> str:
    group_id = _strip(settings.VK_GROUP_ID)
    if not group_id:
        return ''
    return f'https://vk.com/im?sel=-{group_id}&start={urllib.parse.quote(payload)}'


def build_messenger_targets(referrer_user_id: int) -> list[dict[str, str]]:
    payload = build_referral_payload(referrer_user_id)
    raw = [
        {
            'platform': MessengerPlatform.TELEGRAM.value,
            'title': 'Telegram',
            'url': _telegram_link(payload),
        },
        {
            'platform': MessengerPlatform.MAX.value,
            'title': 'MAX',
            'url': _max_link(payload),
        },
        {
            'platform': MessengerPlatform.VK.value,
            'title': 'ВКонтакте',
            'url': _vk_link(payload),
        },
    ]
    return [item for item in raw if item['url']]


def build_switch_targets(bridge_token: str) -> list[dict[str, str]]:
    payload = build_bridge_payload(bridge_token)
    raw = [
        {'platform': MessengerPlatform.TELEGRAM.value, 'title': 'Telegram', 'url': _telegram_link(payload)},
        {'platform': MessengerPlatform.MAX.value, 'title': 'MAX', 'url': _max_link(payload)},
        {'platform': MessengerPlatform.VK.value, 'title': 'ВКонтакте', 'url': _vk_link(payload)},
    ]
    return [item for item in raw if item['url']]
