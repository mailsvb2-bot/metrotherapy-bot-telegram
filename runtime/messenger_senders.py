from __future__ import annotations

import asyncio
import json
import mimetypes
import urllib.parse
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


def _multipart_bytes(field_name: str, filename: str, content: bytes, *, content_type: str) -> tuple[bytes, str]:
    boundary = f'----ChatGPTBoundary{uuid4().hex}'
    head = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        f'Content-Type: {content_type}\r\n\r\n'
    ).encode('utf-8')
    tail = f'\r\n--{boundary}--\r\n'.encode('utf-8')
    return head + content + tail, boundary



def _multipart_upload(url: str, *, token: str, field_name: str, path: Path) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    content = path.read_bytes()
    body, boundary = _multipart_bytes(field_name, path.name, content, content_type=mime_type)
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            'Authorization': token,
            'Content-Type': f'multipart/form-data; boundary={boundary}',
            'Content-Length': str(len(body)),
        },
        method='POST',
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode('utf-8')
    return json.loads(raw) if raw else {}


class TelegramBotSender:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        return await self.bot.send_message(int(external_user_id), text, **kwargs)

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

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        token = (self.token or settings.MAX_BOT_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('MAX_BOT_TOKEN is empty')
        url = f'https://platform-api.max.ru/messages?user_id={urllib.parse.quote(str(external_user_id))}'
        payload: dict[str, Any] = {'text': text}
        if kwargs.get('disable_link_preview') is not None:
            url += f"&disable_link_preview={'true' if kwargs['disable_link_preview'] else 'false'}"
        if kwargs.get('format'):
            payload['format'] = kwargs['format']
        if kwargs.get('notify') is not None:
            payload['notify'] = bool(kwargs['notify'])
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
            'https://platform-api.max.ru/uploads?type=audio',
            method='POST',
            headers={'Authorization': token},
            payload=None,
        )
        upload_url = str(upload_meta.get('url') or '').strip()
        media_token = str(upload_meta.get('token') or '').strip()
        if not upload_url or not media_token:
            raise MessengerTransportError(f'Unexpected MAX upload response: {upload_meta}')
        await asyncio.to_thread(_multipart_upload, upload_url, token=token, field_name='data', path=file_path)
        store_media_token('max', file_path, media_token, media_type='audio')
        return media_token

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        token = (self.token or settings.MAX_BOT_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('MAX_BOT_TOKEN is empty')
        media_token = await self._ensure_audio_token(file_path)
        url = f'https://platform-api.max.ru/messages?user_id={urllib.parse.quote(str(external_user_id))}'
        payload: dict[str, Any] = {
            'text': caption or '',
            'attachments': [{'type': 'audio', 'payload': {'token': media_token}}],
        }
        if kwargs.get('notify') is not None:
            payload['notify'] = bool(kwargs['notify'])
        delays = (0.0, 0.8, 1.6, 2.4)
        last_error: Exception | None = None
        for delay in delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                data = await asyncio.to_thread(_json_request, url, method='POST', headers={'Authorization': token}, payload=payload)
            except (OSError, ValueError, TypeError) as exc:  # pragma: no cover
                last_error = exc
                continue
            if isinstance(data, dict) and data.get('code') == 'attachment.not.ready':
                last_error = MessengerMediaNotReadyError(str(data))
                continue
            if isinstance(data, dict) and data.get('error'):
                raise MessengerTransportError(str(data['error']))
            return data.get('message', data)
        if last_error is not None:
            raise last_error if isinstance(last_error, MessengerTransportError) else MessengerTransportError(str(last_error))
        raise MessengerTransportError('MAX audio send failed without details')


@dataclass
class VkBotSender:
    token: str | None = None
    api_version: str | None = None

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        token = (self.token or settings.VK_GROUP_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('VK_GROUP_TOKEN is empty')
        version = (self.api_version or getattr(settings, 'VK_API_VERSION', '') or '5.199').strip()
        params = {
            'user_id': str(external_user_id),
            'random_id': int(kwargs.get('random_id') or 0),
            'message': text,
            'access_token': token,
            'v': version,
        }
        if kwargs.get('keyboard_json'):
            params['keyboard'] = kwargs['keyboard_json']
        data = await asyncio.to_thread(_form_request, 'https://api.vk.com/method/messages.send', params)
        if isinstance(data, dict) and data.get('error'):
            raise MessengerTransportError(str(data['error']))
        return data.get('response', data)

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        raise MessengerTransportError('VK native audio delivery is not configured in this project build')
