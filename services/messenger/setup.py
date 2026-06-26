from __future__ import annotations

import os
from dataclasses import dataclass

from config.settings import settings
from runtime.telegram_transport import telegram_transport
from services.messenger.bridge import issue_bridge_token
from services.messenger.links import build_switch_targets, build_messenger_targets


@dataclass(frozen=True)
class MessengerSetupStatus:
    telegram_ok: bool
    max_ok: bool
    vk_ok: bool
    webhook_runtime_ok: bool
    public_base_url: str
    vk_webhook_url: str
    max_webhook_url: str
    missing: tuple[str, ...]
    warnings: tuple[str, ...]


def _strip(value: str | None) -> str:
    return (value or '').strip().rstrip('/')


def _app_env() -> str:
    return (os.getenv('APP_ENV') or getattr(settings, 'APP_ENV', '') or 'dev').strip().lower()


def _deployed_env() -> bool:
    return _app_env() in {'prod', 'production', 'stage', 'staging'}


def build_setup_status() -> MessengerSetupStatus:
    public_base = _strip(getattr(settings, 'MESSENGER_PUBLIC_BASE_URL', ''))
    deployed = _deployed_env()
    messenger_webhook_enabled = bool(getattr(settings, 'MESSENGER_WEBHOOK_ENABLED', False))
    telegram_ok = bool(_strip(getattr(settings, 'TELEGRAM_BOT_USERNAME', '')))

    max_link_ready = bool(_strip(getattr(settings, 'MAX_BOT_LINK_BASE', '')))
    max_token_ready = bool(_strip(getattr(settings, 'MAX_BOT_TOKEN', '')))
    max_secret_ready = bool(_strip(getattr(settings, 'MAX_WEBHOOK_SECRET', '')))
    max_ok = bool(max_link_ready and max_token_ready and (not (deployed and messenger_webhook_enabled) or max_secret_ready))

    vk_core_ok = bool(
        _strip(getattr(settings, 'VK_GROUP_ID', ''))
        and _strip(getattr(settings, 'VK_GROUP_TOKEN', ''))
        and _strip(getattr(settings, 'VK_CONFIRMATION_TOKEN', ''))
    )
    vk_secret_ready = bool(_strip(getattr(settings, 'VK_SECRET', '')))
    vk_ok = bool(vk_core_ok and (not (deployed and messenger_webhook_enabled) or vk_secret_ready))

    messenger_runtime_ok = bool(
        messenger_webhook_enabled
        and public_base
        and (max_ok or vk_ok)
    )
    telegram_transport_mode = telegram_transport()
    telegram_webhook_ok = bool(
        telegram_transport_mode == 'webhook'
        and _strip(getattr(settings, 'TELEGRAM_WEBHOOK_PUBLIC_BASE_URL', ''))
    )
    webhook_runtime_ok = bool(messenger_runtime_ok or telegram_webhook_ok)

    missing: list[str] = []
    warnings: list[str] = []
    if not telegram_ok:
        missing.append('TELEGRAM_BOT_USERNAME')
    if not max_link_ready:
        missing.append('MAX_BOT_LINK_BASE')
    if not max_token_ready:
        missing.append('MAX_BOT_TOKEN')
    if deployed and messenger_webhook_enabled and not max_secret_ready:
        missing.append('MAX_WEBHOOK_SECRET')
    if not _strip(getattr(settings, 'VK_GROUP_ID', '')):
        missing.append('VK_GROUP_ID')
    if not _strip(getattr(settings, 'VK_GROUP_TOKEN', '')):
        missing.append('VK_GROUP_TOKEN')
    if not _strip(getattr(settings, 'VK_CONFIRMATION_TOKEN', '')):
        missing.append('VK_CONFIRMATION_TOKEN')
    if deployed and messenger_webhook_enabled and not vk_secret_ready:
        missing.append('VK_SECRET')
    if not public_base:
        missing.append('MESSENGER_PUBLIC_BASE_URL')
    if not messenger_webhook_enabled and not telegram_webhook_ok:
        missing.append('MESSENGER_WEBHOOK_ENABLED=1 or TELEGRAM_TRANSPORT=webhook')
    if _strip(getattr(settings, 'MAX_BOT_LINK_BASE', '')) and '{payload}' not in _strip(getattr(settings, 'MAX_BOT_LINK_BASE', '')):
        warnings.append('MAX_BOT_LINK_BASE не содержит {payload}; проект добавит ?start=..., но шаблон с {payload} надёжнее.')
    if vk_core_ok and not vk_secret_ready and not deployed:
        warnings.append('VK_SECRET пустой; в dev webhook будет работать, но подпись входящих событий не усилена секретом.')
    if max_token_ready and not max_secret_ready and not deployed:
        warnings.append('MAX_WEBHOOK_SECRET пустой; в dev webhook будет работать, но подпись входящих событий не усилена секретом.')
    if vk_ok:
        warnings.append('Для VK callback-кнопок включите в Callback API тип события message_event / «Событие сообщения».')
    if public_base and not (public_base.startswith('https://') or public_base.startswith('http://')):
        warnings.append('MESSENGER_PUBLIC_BASE_URL должен быть полным URL, например https://your-domain.tld')
    telegram_public = _strip(getattr(settings, 'TELEGRAM_WEBHOOK_PUBLIC_BASE_URL', ''))
    if telegram_transport_mode == 'webhook' and not telegram_public:
        missing.append('TELEGRAM_WEBHOOK_PUBLIC_BASE_URL')
    if telegram_public and not (telegram_public.startswith('https://') or telegram_public.startswith('http://')):
        warnings.append('TELEGRAM_WEBHOOK_PUBLIC_BASE_URL должен быть полным URL, например https://your-domain.tld')

    vk_webhook_url = f'{public_base}/webhooks/vk' if public_base else ''
    max_webhook_url = f'{public_base}/webhooks/max' if public_base else ''
    return MessengerSetupStatus(
        telegram_ok=telegram_ok,
        max_ok=max_ok,
        vk_ok=vk_ok,
        webhook_runtime_ok=webhook_runtime_ok,
        public_base_url=public_base,
        vk_webhook_url=vk_webhook_url,
        max_webhook_url=max_webhook_url,
        missing=tuple(missing),
        warnings=tuple(warnings),
    )


def render_setup_text() -> str:
    status = build_setup_status()
    lines = ['🔧 Настройка multi-messenger', '']
    lines.append(f"Telegram referral/switch links: {'✅' if status.telegram_ok else '❌'}")
    lines.append(f"MAX link + sender: {'✅' if status.max_ok else '❌'}")
    lines.append(f"VK link + sender: {'✅' if status.vk_ok else '❌'}")
    lines.append(f"Webhook runtime: {'✅' if status.webhook_runtime_ok else '❌'}")
    lines.append('')
    if status.public_base_url:
        lines.append(f'Public base URL: {status.public_base_url}')
        lines.append(f'VK webhook URL: {status.vk_webhook_url}')
        lines.append(f'MAX webhook URL: {status.max_webhook_url}')
        lines.append('')
    lines.append('Как это работает:')
    lines.append('1) Пользователь в Telegram нажимает переход в VK/MAX.')
    lines.append('2) Открывается ссылка с start-параметром bridge/ref.')
    lines.append('3) VK/MAX webhook получает входящее сообщение и сам фиксирует внешний user id.')
    lines.append('4) Ручной ввод VK ID / MAX ID пользователю не нужен.')
    lines.append('5) Для новых VK callback-кнопок в Callback API должен быть включён тип события message_event.')
    lines.append('')
    if status.missing:
        lines.append('Не хватает переменных:')
        for item in status.missing:
            lines.append(f'• {item}')
    else:
        lines.append('Все основные переменные для Telegram/VK/MAX заданы.')
    if status.warnings:
        lines.append('')
        lines.append('Предупреждения:')
        for item in status.warnings:
            lines.append(f'• {item}')
    return '\n'.join(lines)


def render_setup_links_preview(user_id: int) -> str:
    token = issue_bridge_token(int(user_id), purpose='switch')
    switch_targets = build_switch_targets(token)
    referral_targets = build_messenger_targets(int(user_id))
    lines = ['🔗 Предпросмотр ссылок', '']
    if switch_targets:
        lines.append('Переход в другой мессенджер:')
        for item in switch_targets:
            lines.append(f"• {item['title']}: {item['url']}")
        lines.append('')
    else:
        lines.append('Ссылки перехода пока не строятся — не хватает переменных окружения.')
        lines.append('')
    if referral_targets:
        lines.append('Реферальные / share ссылки:')
        for item in referral_targets:
            lines.append(f"• {item['title']}: {item['url']}")
    else:
        lines.append('Реферальные / share ссылки пока не строятся.')
    lines.append('')
    lines.append('Пользователю не нужно вручную вводить VK ID / MAX ID: внешний id фиксируется entrypoint/webhook-слоем после перехода по ссылке.')
    return '\n'.join(lines)


def validate_setup(strict: bool = False) -> tuple[bool, str]:
    status = build_setup_status()
    text = render_setup_text()
    ok = not status.missing
    if strict and status.warnings:
        ok = False
    return ok, text
