from __future__ import annotations

from dataclasses import dataclass

from config.settings import settings
from .bridge import issue_bridge_token
from .links import build_switch_targets, build_messenger_targets


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


def build_setup_status() -> MessengerSetupStatus:
    public_base = _strip(getattr(settings, 'MESSENGER_PUBLIC_BASE_URL', ''))
    telegram_ok = bool(_strip(getattr(settings, 'TELEGRAM_BOT_USERNAME', '')))
    max_ok = bool(
        _strip(getattr(settings, 'MAX_BOT_LINK_BASE', ''))
        and _strip(getattr(settings, 'MAX_BOT_TOKEN', ''))
    )
    vk_ok = bool(
        _strip(getattr(settings, 'VK_GROUP_ID', ''))
        and _strip(getattr(settings, 'VK_GROUP_TOKEN', ''))
        and _strip(getattr(settings, 'VK_CONFIRMATION_TOKEN', ''))
    )
    webhook_runtime_ok = bool(
        getattr(settings, 'MESSENGER_WEBHOOK_ENABLED', False)
        and public_base
        and (max_ok or vk_ok)
    )

    missing: list[str] = []
    warnings: list[str] = []
    if not telegram_ok:
        missing.append('TELEGRAM_BOT_USERNAME')
    if not _strip(getattr(settings, 'MAX_BOT_LINK_BASE', '')):
        missing.append('MAX_BOT_LINK_BASE')
    if not _strip(getattr(settings, 'MAX_BOT_TOKEN', '')):
        missing.append('MAX_BOT_TOKEN')
    if not _strip(getattr(settings, 'VK_GROUP_ID', '')):
        missing.append('VK_GROUP_ID')
    if not _strip(getattr(settings, 'VK_GROUP_TOKEN', '')):
        missing.append('VK_GROUP_TOKEN')
    if not _strip(getattr(settings, 'VK_CONFIRMATION_TOKEN', '')):
        missing.append('VK_CONFIRMATION_TOKEN')
    if not public_base:
        missing.append('MESSENGER_PUBLIC_BASE_URL')
    if not getattr(settings, 'MESSENGER_WEBHOOK_ENABLED', False):
        missing.append('MESSENGER_WEBHOOK_ENABLED=1')
    if _strip(getattr(settings, 'MAX_BOT_LINK_BASE', '')) and '{payload}' not in _strip(getattr(settings, 'MAX_BOT_LINK_BASE', '')):
        warnings.append('MAX_BOT_LINK_BASE не содержит {payload}; проект добавит ?start=..., но шаблон с {payload} надёжнее.')
    if public_base and not (public_base.startswith('https://') or public_base.startswith('http://')):
        warnings.append('MESSENGER_PUBLIC_BASE_URL должен быть полным URL, например https://your-domain.tld')

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
    lines.append('')
    if status.missing:
        lines.append('Не хватает переменных:')
        for item in status.missing:
            lines.append(f'• {item}')
    else:
        lines.append('Все основные переменные для Telegram/VK/MAX заданы.')
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
