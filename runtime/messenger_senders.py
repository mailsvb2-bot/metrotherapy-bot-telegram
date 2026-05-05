from __future__ import annotations

import asyncio
import json
import mimetypes
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:  # pragma: no cover
    from aiogram import Bot
else:
    Bot = Any

from config.settings import settings
from services.messenger.media_assets import get_cached_media_token, store_media_token


class MessengerTransportError(RuntimeError):
    pass


class MessengerMediaNotReadyError(MessengerTransportError):
    pass


def _json_request(url: str, *, method: str = 'POST', headers: dict[str, str] | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None
    req_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        req_headers.setdefault('Content-Type', 'application/json')
    request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read().decode('utf-8')
    return json.loads(raw) if raw else {}


def _form_request(url: str, params: dict[str, Any]) -> dict[str, Any]:
    encoded = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode('utf-8')
    request = urllib.request.Request(url, data=encoded, method='POST')
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read().decode('utf-8')
    return json.loads(raw) if raw else {}


def _multipart_bytes(
    field_name: str,
    filename: str,
    content: bytes,
    *,
    content_type: str | None = None,
) -> tuple[bytes, str]:
    boundary = f'----ChatGPTBoundary{uuid4().hex}'
    disposition = f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
    type_header = f'Content-Type: {content_type}\r\n' if content_type else ''
    head = (
        f'--{boundary}\r\n'
        f'{disposition}'
        f'{type_header}'
        f'\r\n'
    ).encode('utf-8')
    tail = f'\r\n--{boundary}--\r\n'.encode('utf-8')
    return head + content + tail, boundary



def _multipart_upload(
    url: str,
    *,
    token: str | None = None,
    field_name: str,
    path: Path,
    include_part_content_type: bool = True,
) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    content = path.read_bytes()
    body, boundary = _multipart_bytes(
        field_name,
        path.name,
        content,
        content_type=mime_type if include_part_content_type else None,
    )
    headers = {
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Content-Length': str(len(body)),
    }
    if token:
        headers['Authorization'] = token
    request = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode('utf-8')
    return json.loads(raw) if raw else {}


def _max_multipart_upload(url: str, *, token: str | None = None, field_name: str, path: Path) -> dict[str, Any]:
    """Upload to MAX upload URL with a curl-compatible multipart fallback.

    MAX docs show `curl -F "data=@file"`, which does not force a per-part MIME
    type in the way our hand-built multipart body did. Some MAX upload URLs
    return HTTP 415 for .opus when the file part contains `Content-Type`.
    Retry without the per-part MIME header before surfacing the error.
    """
    try:
        return _multipart_upload(
            url,
            token=token,
            field_name=field_name,
            path=path,
            include_part_content_type=True,
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 415:
            raise
        return _multipart_upload(
            url,
            token=token,
            field_name=field_name,
            path=path,
            include_part_content_type=False,
        )


class TelegramBotSender:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        return await self.bot.send_message(int(external_user_id), text, **kwargs)

    # MAX_NATIVE_AUDIO_HONEST_FAILURE_V1
    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        from services.fast_send_audio import send_audio_cached
        return await send_audio_cached(
            self.bot,
            int(external_user_id),
            key=f'cross_audio:{file_path.name}',
            file_path=file_path,
            caption=caption or '',
        )


@dataclass
class MaxBotSender:
    token: str | None = None

    @staticmethod
    def _api_base_url() -> str:
        return str(getattr(settings, 'MAX_API_BASE_URL', '') or 'https://platform-api.max.ru').strip().rstrip('/')

    @staticmethod
    def _payment_public_base_url() -> str:
        base = (
            getattr(settings, 'PAYMENT_PUBLIC_BASE_URL', '')
            or getattr(settings, 'MESSENGER_PUBLIC_BASE_URL', '')
            or 'https://metrotherapy-bot.metrotherapy.ru'
        )
        return str(base).strip().rstrip('/')

    @classmethod
    def _payment_url(cls, *, external_user_id: str, kind: str) -> str:
        query = urllib.parse.urlencode(
            {
                'source': 'max',
                'user_id': str(external_user_id),
                'kind': kind,
            }
        )
        return f'{cls._payment_public_base_url()}/pay/yookassa?{query}'


    @staticmethod
    def _message_button(text: str, command: str) -> dict[str, Any]:
        # MAX message buttons send the text back to the bot. The payload keeps
        # the canonical command visible for clients that preserve it in webhook
        # updates, while text remains compatible with services.messenger.text_ui.
        return {'type': 'message', 'text': text, 'payload': command}

    @staticmethod
    def _link_button(text: str, url: str) -> dict[str, Any]:
        return {'type': 'link', 'text': text, 'url': url}

    @classmethod
    def _inline_keyboard(cls, rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
        return {'type': 'inline_keyboard', 'payload': {'buttons': rows}}

    @classmethod
    def _main_keyboard(cls, *, external_user_id: str) -> dict[str, Any]:
        # Mirrors keyboards.inline.kb_main(), except pay/gift are real links in
        # MAX because Telegram payment callbacks cannot execute inside MAX.
        return cls._inline_keyboard([
            [
                cls._message_button('🌿 Попробовать бесплатно', 'demo'),
                cls._message_button('🔐 Полный маршрут', 'full'),
            ],
            [
                cls._link_button('💳 Тарифы', cls._payment_url(external_user_id=external_user_id, kind='subscription')),
                cls._link_button('🎁 Подарить', cls._payment_url(external_user_id=external_user_id, kind='gift')),
            ],
            [
                cls._message_button('📈 Мой прогресс', 'progress'),
                cls._message_button('🧠 Настройки', 'settings'),
            ],
            [
                cls._message_button('📣 Посоветовать', 'share'),
                cls._message_button('🌤 Погода', 'weather'),
            ],
        ])

    @classmethod
    def _demo_kind_keyboard(cls) -> dict[str, Any]:
        return cls._inline_keyboard([
            [cls._message_button('🚗 Практика на утро / дорогу', 'demo_work')],
            [cls._message_button('🌙 Практика на вечер / домой', 'demo_home')],
            [cls._message_button('⬅️ Назад', 'start')],
        ])

    @classmethod
    def _score_scale_keyboard(cls) -> dict[str, Any]:
        # MAX clients can visually truncate wide inline rows.
        # Keep the full -10..+10 scale, but render it in compact rows so every
        # number stays visible. Users may also type the number manually.
        vals = list(range(-10, 11))
        rows: list[list[dict[str, Any]]] = []
        for i in range(0, len(vals), 3):
            rows.append([
                cls._message_button(str(value), str(value))
                for value in vals[i:i + 3]
            ])
        rows.append([cls._message_button('⬅️ Меню', 'start')])
        return cls._inline_keyboard(rows)

    @classmethod
    def _full_route_keyboard(cls) -> dict[str, Any]:
        return cls._inline_keyboard([
            [
                cls._message_button('🎧 Получить аудио', 'continue'),
                cls._message_button('✅ Прослушал', 'done'),
            ],
            [cls._message_button('⬅️ Меню', 'start')],
        ])

    @classmethod
    def _weather_keyboard(cls) -> dict[str, Any]:
        return cls._inline_keyboard([
            [
                cls._message_button('🔄 Обновить погоду', 'weather'),
                cls._message_button('🏙 Изменить город', 'weather_city'),
            ],
            [cls._message_button('⬅️ Меню', 'start')],
        ])

    @classmethod
    def _weather_city_keyboard(cls) -> dict[str, Any]:
        return cls._inline_keyboard([[cls._message_button('⬅️ Меню', 'start')]])

    @classmethod
    def _after_audio_keyboard(cls) -> dict[str, Any]:
        return cls._inline_keyboard([
            [
                cls._message_button('✅ Прослушал', 'done'),
                cls._message_button('🔁 Повторить аудио', 'repeat'),
            ],
            [cls._message_button('⬅️ Меню', 'start')],
        ])

    @classmethod
    def _keyboard_for_text(cls, text: str, *, external_user_id: str) -> dict[str, Any] | None:
        clean = (text or '').lstrip()
        lowered = clean.casefold()
        if clean.startswith('Главное меню'):
            return cls._main_keyboard(external_user_id=external_user_id)
        if clean.startswith('🌿 Бесплатная практика'):
            return cls._demo_kind_keyboard()
        if clean.startswith('🔐 Полный маршрут'):
            return cls._full_route_keyboard()
        if clean.startswith('🌤 Погода'):
            return cls._weather_keyboard()
        if clean.startswith('🏙 Напишите название города'):
            return cls._weather_city_keyboard()
        if 'шкала оценки' in lowered or 'оцените состояние сейчас' in lowered:
            return cls._score_scale_keyboard()
        if 'после оплаты вернитесь сюда' in lowered or 'аудио придёт' in lowered or 'аудио:' in lowered:
            return cls._after_audio_keyboard()
        return None

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        token = (self.token or settings.MAX_BOT_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('MAX_BOT_TOKEN is empty')
        url = f'{self._api_base_url()}/messages?user_id={urllib.parse.quote(str(external_user_id))}'
        payload: dict[str, Any] = {'text': text}
        if kwargs.get('disable_link_preview') is not None:
            url += f"&disable_link_preview={'true' if kwargs['disable_link_preview'] else 'false'}"
        if kwargs.get('format'):
            payload['format'] = kwargs['format']
        if kwargs.get('notify') is not None:
            payload['notify'] = bool(kwargs['notify'])
        keyboard = kwargs.get('max_keyboard') or self._keyboard_for_text(str(text or ''), external_user_id=str(external_user_id))
        if keyboard is not None:
            payload.setdefault('attachments', []).append(keyboard)
        data = await asyncio.to_thread(_json_request, url, method='POST', headers={'Authorization': token}, payload=payload)
        if isinstance(data, dict) and data.get('error'):
            err = data['error']
            raise MessengerTransportError(str(err))
        if isinstance(data, dict) and data.get('message') is not None:
            return data['message']
        return data

    async def _ensure_audio_token(self, file_path: Path) -> str:
        cached = get_cached_media_token('max', file_path, media_type='audio')
        if cached is not None:
            return cached.remote_token
        token = (self.token or settings.MAX_BOT_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('MAX_BOT_TOKEN is empty')
        upload_meta = await asyncio.to_thread(
            _json_request,
            f'{self._api_base_url()}/uploads?type=audio',
            method='POST',
            headers={'Authorization': token},
            payload=None,
        )
        upload_url = str(upload_meta.get('url') or '').strip()
        media_token = str(upload_meta.get('token') or '').strip()
        if not upload_url or not media_token:
            raise MessengerTransportError(f'Unexpected MAX upload response: {upload_meta}')
        await asyncio.to_thread(_max_multipart_upload, upload_url, token=token, field_name='data', path=file_path)
        store_media_token('max', file_path, media_token, media_type='audio')
        return media_token

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        token = (self.token or settings.MAX_BOT_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('MAX_BOT_TOKEN is empty')

        try:
            media_token = await self._ensure_audio_token(file_path)
        except urllib.error.HTTPError as exc:
            # MAX native audio upload may reject source files with HTTP 415.
            # Do not build /audio/<filename> here.
            # The canonical layer services.messenger.audio_delivery owns fallback:
            # /media/audio/access/<token>
            raise MessengerTransportError(f'MAX audio upload failed: HTTP {exc.code}') from exc
        except (OSError, ValueError, TypeError) as exc:
            raise MessengerTransportError(f'MAX audio upload failed: {exc}') from exc

        url = f'{self._api_base_url()}/messages?user_id={urllib.parse.quote(str(external_user_id))}'
        payload: dict[str, Any] = {
            'text': caption or '',
            'attachments': [
                {'type': 'audio', 'payload': {'token': media_token}},
                self._after_audio_keyboard(),
            ],
        }
        if kwargs.get('notify') is not None:
            payload['notify'] = bool(kwargs['notify'])

        delays = (0.0, 0.8, 1.6, 2.4)
        last_error: Exception | None = None
        for delay in delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                data = await asyncio.to_thread(
                    _json_request,
                    url,
                    method='POST',
                    headers={'Authorization': token},
                    payload=payload,
                )
            except urllib.error.HTTPError as exc:
                last_error = MessengerTransportError(f'MAX audio send failed: HTTP {exc.code}')
                continue
            except (OSError, ValueError, TypeError) as exc:
                last_error = exc
                continue

            if isinstance(data, dict) and data.get('code') == 'attachment.not.ready':
                last_error = MessengerMediaNotReadyError(str(data))
                continue
            if isinstance(data, dict) and data.get('error'):
                last_error = MessengerTransportError(str(data['error']))
                continue
            return data.get('message', data)

        if last_error is not None:
            raise last_error if isinstance(last_error, MessengerTransportError) else MessengerTransportError(str(last_error))
        raise MessengerTransportError('MAX audio send failed without details')
class VkBotSender:
    token: str | None = None
    api_version: str | None = None

    def _token(self) -> str:
        token = (self.token or settings.VK_GROUP_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('VK_GROUP_TOKEN is empty')
        return token

    def _api_version(self) -> str:
        return (self.api_version or getattr(settings, 'VK_API_VERSION', '') or '5.199').strip()

    @staticmethod
    def _button_command(button: Any) -> str:
        if not isinstance(button, dict):
            return ''
        action = button.get('action') or {}
        payload = action.get('payload')
        if isinstance(payload, str) and payload.strip():
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, dict):
                command = decoded.get('command') or decoded.get('cmd') or decoded.get('action')
                if isinstance(command, str) and command.strip():
                    return command.strip()
        label = str(action.get('label') or '').strip().casefold().replace('ё', 'е')
        label_aliases = {
            '🌿 попробовать бесплатно': 'demo',
            '🔐 полный маршрут': 'full',
            '💳 тарифы': 'pay',
            '🎁 подарить': 'gift',
            '📈 мой прогресс': 'progress',
            '🧠 настройки': 'settings',
            '📣 посоветовать': 'share',
            '🌤 погода': 'weather',
            '🎧 получить аудио': 'continue',
            '✅ прослушал': 'done',
        }
        return label_aliases.get(label, '')

    @staticmethod
    def _payment_public_base_url() -> str:
        base = (
            getattr(settings, 'PAYMENT_PUBLIC_BASE_URL', '')
            or getattr(settings, 'MESSENGER_PUBLIC_BASE_URL', '')
            or 'https://metrotherapy-bot.metrotherapy.ru'
        )
        return str(base).strip().rstrip('/')

    @classmethod
    def _payment_url(cls, *, source: str, external_user_id: str, kind: str) -> str:
        query = urllib.parse.urlencode(
            {
                'source': source,
                'user_id': str(external_user_id),
                'kind': kind,
            }
        )
        return f'{cls._payment_public_base_url()}/pay/yookassa?{query}'

    @staticmethod
    def _vk_text_button(label: str, command: str, color: str = 'secondary') -> dict[str, Any]:
        return {
            'action': {
                'type': 'text',
                'label': label,
                'payload': json.dumps({'command': command}, ensure_ascii=False),
            },
            'color': color,
        }

    @staticmethod
    def _vk_open_link_button(label: str, link: str, color: str = 'primary') -> dict[str, Any]:
        # VK rejects color on open_link buttons with error_code=911.
        # Keep the color argument for backward-compatible call sites, but do not render it.
        return {
            'action': {
                'type': 'open_link',
                'label': label,
                'link': link,
            },
        }

    @classmethod
    def _telegram_main_parity_keyboard_json(cls, keyboard_json: str) -> str:
        """Keep VK main keyboard contract aligned with Telegram kb_main().

        VK currently receives keyboard JSON from runtime/messenger_webhooks.py.
        Older builds appended VK-only continuation controls to the persistent
        main keyboard. That made VK's visible button surface diverge from the
        Telegram main menu. We normalize only that full main-menu keyboard at
        the transport boundary and leave contextual keyboards untouched.
        """
        try:
            keyboard = json.loads(keyboard_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return keyboard_json
        if not isinstance(keyboard, dict):
            return keyboard_json
        rows = keyboard.get('buttons')
        if not isinstance(rows, list):
            return keyboard_json

        all_commands: set[str] = set()
        row_commands: list[tuple[list[Any], set[str]]] = []
        for row in rows:
            if not isinstance(row, list):
                row_commands.append((row, set()))
                continue
            commands = {cls._button_command(button) for button in row}
            commands.discard('')
            all_commands.update(commands)
            row_commands.append((row, commands))

        telegram_main_commands = {
            'demo',
            'full',
            'pay',
            'gift',
            'progress',
            'settings',
            'share',
            'weather',
        }
        vk_only_main_controls = {'continue', 'done'}
        if not telegram_main_commands.issubset(all_commands):
            return keyboard_json
        if not vk_only_main_controls.intersection(all_commands):
            return keyboard_json

        filtered_rows = [
            row
            for row, commands in row_commands
            if not commands or not commands.issubset(vk_only_main_controls)
        ]
        normalized = dict(keyboard)
        normalized['buttons'] = filtered_rows
        return json.dumps(normalized, ensure_ascii=False, separators=(',', ':'))

    @classmethod
    def _with_vk_payment_links(cls, keyboard_json: str, *, external_user_id: str) -> str:
        """Make VK pay/gift buttons functional even when text UI lacks callbacks.

        Telegram has callback handlers for tariff/gift menus. VK text keyboards
        cannot run Telegram callbacks directly, so these main-menu buttons must
        open the same payment boundary instead of falling back to menu text.
        """
        try:
            keyboard = json.loads(keyboard_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return keyboard_json
        if not isinstance(keyboard, dict) or not isinstance(keyboard.get('buttons'), list):
            return keyboard_json

        changed = False
        for row in keyboard['buttons']:
            if not isinstance(row, list):
                continue
            for idx, button in enumerate(row):
                command = cls._button_command(button)
                if command == 'pay':
                    row[idx] = cls._vk_open_link_button(
                        '💳 Тарифы',
                        cls._payment_url(source='vk', external_user_id=external_user_id, kind='subscription'),
                        'primary',
                    )
                    changed = True
                elif command == 'gift':
                    row[idx] = cls._vk_open_link_button(
                        '🎁 Подарить',
                        cls._payment_url(source='vk', external_user_id=external_user_id, kind='gift'),
                        'secondary',
                    )
                    changed = True
        if not changed:
            return keyboard_json
        return json.dumps(keyboard, ensure_ascii=False, separators=(',', ':'))

    @classmethod
    def _full_route_keyboard_json(cls) -> str:
        """Contextual VK keyboard for the full-route branch.

        Main VK menu is kept 1:1 with Telegram kb_main(); continuation controls
        are shown only where the full-route text explicitly asks for them.
        """
        return json.dumps(
            {
                'one_time': False,
                'inline': False,
                'buttons': [
                    [
                        cls._vk_text_button('🎧 Получить аудио', 'continue', 'primary'),
                        cls._vk_text_button('✅ Прослушал', 'done', 'positive'),
                    ],
                    [cls._vk_text_button('⬅️ Меню', 'start', 'secondary')],
                ],
            },
            ensure_ascii=False,
            separators=(',', ':'),
        )

    @classmethod
    def _prepare_vk_keyboard_json(cls, keyboard_json: str, *, external_user_id: str, text: str) -> str:
        if (text or '').lstrip().startswith('🔐 Полный маршрут'):
            return cls._full_route_keyboard_json()
        normalized = cls._telegram_main_parity_keyboard_json(keyboard_json)
        return cls._with_vk_payment_links(normalized, external_user_id=external_user_id)

    async def _vk_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        data = await asyncio.to_thread(
            _form_request,
            f'https://api.vk.com/method/{method}',
            {
                **params,
                'access_token': self._token(),
                'v': self._api_version(),
            },
        )
        if isinstance(data, dict) and data.get('error'):
            raise MessengerTransportError(str(data['error']))
        return data

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        random_id = kwargs.get('random_id')
        if random_id is None:
            random_id = int(time.time_ns() % 2147483647)
        params = {
            'user_id': str(external_user_id),
            'random_id': int(random_id),
            'message': text,
        }
        if kwargs.get('keyboard_json'):
            params['keyboard'] = self._prepare_vk_keyboard_json(
                str(kwargs['keyboard_json']),
                external_user_id=str(external_user_id),
                text=str(text or ''),
            )
        if kwargs.get('attachment'):
            params['attachment'] = kwargs['attachment']
        data = await self._vk_method('messages.send', params)
        return data.get('response', data)

    @staticmethod
    def _doc_attachment_from_save_response(data: dict[str, Any]) -> str:
        """
        VK docs.save can return either a normal doc or an audio_message object.

        For messages.send the stable attachment reference is still doc<owner_id>_<id>
        with optional access_key. This lets VK deliver .opus/.ogg as a native
        audio message instead of forcing a Telegram/web fallback.
        """
        response = data.get('response')
        doc: dict[str, Any] | None = None

        def pick(candidate: Any) -> dict[str, Any] | None:
            if not isinstance(candidate, dict):
                return None

            if isinstance(candidate.get('doc'), dict):
                return candidate['doc']

            if isinstance(candidate.get('audio_message'), dict):
                return candidate['audio_message']

            if candidate.get('type') in {'doc', 'audio_message'}:
                nested = candidate.get(str(candidate.get('type')))
                if isinstance(nested, dict):
                    return nested
                return candidate

            if candidate.get('owner_id') is not None and candidate.get('id') is not None:
                return candidate

            return None

        if isinstance(response, dict):
            doc = pick(response)
        elif isinstance(response, list) and response:
            for item in response:
                doc = pick(item)
                if doc is not None:
                    break

        if not doc:
            raise MessengerTransportError(f'Unexpected VK docs.save response: {data}')

        owner_id = doc.get('owner_id')
        doc_id = doc.get('id')
        access_key = str(doc.get('access_key') or '').strip()

        if owner_id is None or doc_id is None:
            raise MessengerTransportError(f'VK saved doc has no owner_id/id: {data}')

        attachment = f'doc{owner_id}_{doc_id}'
        if access_key:
            attachment += f'_{access_key}'

        return attachment

    @staticmethod
    def _vk_upload_type_for_audio(file_path: Path) -> str:
        suffix = file_path.suffix.lower()
        if suffix in {'.opus', '.ogg'}:
            return 'audio_message'
        return 'doc'

    async def _ensure_doc_attachment(self, external_user_id: str, file_path: Path, *, media_type: str | None = None) -> str:
        upload_type = self._vk_upload_type_for_audio(file_path)
        cache_media_type = media_type or f'audio:{upload_type}'

        cached = get_cached_media_token('vk', file_path, media_type=cache_media_type)
        if cached is not None:
            return cached.remote_token

        upload_meta = await self._vk_method(
            'docs.getMessagesUploadServer',
            {
                'peer_id': str(external_user_id),
                'type': upload_type,
            },
        )

        upload_url = str((upload_meta.get('response') or {}).get('upload_url') or '').strip()
        if not upload_url:
            raise MessengerTransportError(f'Unexpected VK docs.getMessagesUploadServer response: {upload_meta}')

        uploaded = await asyncio.to_thread(_multipart_upload, upload_url, field_name='file', path=file_path)
        uploaded_file = str(uploaded.get('file') or '').strip()
        if not uploaded_file:
            raise MessengerTransportError(f'Unexpected VK upload response for type={upload_type}: {uploaded}')

        saved = await self._vk_method(
            'docs.save',
            {
                'file': uploaded_file,
                'title': file_path.stem[:128],
                'tags': 'metrotherapy,audio',
            },
        )

        attachment = self._doc_attachment_from_save_response(saved)
        store_media_token('vk', file_path, attachment, media_type=cache_media_type)
        return attachment

    async def send_document_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        attachment = await self._ensure_doc_attachment(
            str(external_user_id),
            file_path,
            media_type=f'doc:{file_path.suffix.lower() or "file"}',
        )
        return await self.send_text(
            external_user_id,
            caption or file_path.stem,
            attachment=attachment,
            **kwargs,
        )

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        # VK canonical audio delivery uses the VK document upload path.
        # Do not use MAX token/API/attachment format here.
        return await self.send_document_file(
            external_user_id,
            file_path,
            caption=caption,
            **kwargs,
        )
