from __future__ import annotations

import asyncio
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from aiogram import Bot
else:
    Bot = Any

from config.settings import settings
from runtime import messenger_max_ui as max_ui
from runtime.messenger_vk_ui import prepare_vk_keyboard_json
from services.messenger.media_assets import get_cached_media_token, store_media_token
from services.messenger.provider_transport import form_request, json_request, multipart_upload


class MessengerTransportError(RuntimeError):
    pass


class MessengerMediaNotReadyError(MessengerTransportError):
    pass


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

    _main_menu_attachment = staticmethod(max_ui.main_menu_attachment)
    _demo_kind_attachment = staticmethod(max_ui.demo_kind_attachment)

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        token = (self.token or settings.MAX_BOT_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('MAX_BOT_TOKEN is empty')
        url = f'https://platform-api.max.ru/messages?user_id={urllib.parse.quote(str(external_user_id))}'
        attachments = list(kwargs.get('attachments') or max_ui.native_keyboard_attachments(str(text or '')))
        payload: dict[str, Any] = {'text': max_ui.prepare_text(text, has_native_keyboard=bool(attachments))}
        if attachments:
            payload['attachments'] = attachments
        if kwargs.get('disable_link_preview') is not None:
            url += f"&disable_link_preview={'true' if kwargs['disable_link_preview'] else 'false'}"
        if kwargs.get('format'):
            payload['format'] = kwargs['format']
        if kwargs.get('notify') is not None:
            payload['notify'] = bool(kwargs['notify'])
        data = await asyncio.to_thread(json_request, url, method='POST', headers={'Authorization': token}, payload=payload)
        if isinstance(data, dict) and data.get('error'):
            raise MessengerTransportError(str(data['error']))
        return data['message'] if isinstance(data, dict) and data.get('message') is not None else data

    async def _ensure_audio_token(self, file_path: Path) -> str:
        cached = get_cached_media_token('max', file_path, media_type='audio')
        if cached is not None:
            return cached.remote_token
        token = (self.token or settings.MAX_BOT_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('MAX_BOT_TOKEN is empty')
        upload_meta = await asyncio.to_thread(json_request, 'https://platform-api.max.ru/uploads?type=audio', method='POST', headers={'Authorization': token}, payload=None)
        upload_url = str(upload_meta.get('url') or '').strip()
        media_token = str(upload_meta.get('token') or '').strip()
        if not upload_url or not media_token:
            raise MessengerTransportError(f'Unexpected MAX upload response: {upload_meta}')
        await asyncio.to_thread(multipart_upload, upload_url, token=token, field_name='data', path=file_path)
        store_media_token('max', file_path, media_token, media_type='audio')
        return media_token

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        token = (self.token or settings.MAX_BOT_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('MAX_BOT_TOKEN is empty')
        media_token = await self._ensure_audio_token(file_path)
        url = f'https://platform-api.max.ru/messages?user_id={urllib.parse.quote(str(external_user_id))}'
        payload: dict[str, Any] = {'text': caption or '', 'attachments': [{'type': 'audio', 'payload': {'token': media_token}}]}
        if kwargs.get('notify') is not None:
            payload['notify'] = bool(kwargs['notify'])
        delays = (0.0, 0.8, 1.6, 2.4)
        last_error: Exception | None = None
        for delay in delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                data = await asyncio.to_thread(json_request, url, method='POST', headers={'Authorization': token}, payload=payload)
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

    def _token(self) -> str:
        token = (self.token or settings.VK_GROUP_TOKEN or '').strip()
        if not token:
            raise MessengerTransportError('VK_GROUP_TOKEN is empty')
        return token

    def _api_version(self) -> str:
        return (self.api_version or getattr(settings, 'VK_API_VERSION', '') or '5.199').strip()

    async def _vk_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        data = await asyncio.to_thread(form_request, f'https://api.vk.com/method/{method}', {**params, 'access_token': self._token(), 'v': self._api_version()})
        if isinstance(data, dict) and data.get('error'):
            raise MessengerTransportError(str(data['error']))
        return data

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        random_id = kwargs.get('random_id')
        if random_id is None:
            random_id = int(time.time_ns() % 2147483647)
        params = {'user_id': str(external_user_id), 'random_id': int(random_id), 'message': text}
        if kwargs.get('keyboard_json'):
            params['keyboard'] = prepare_vk_keyboard_json(str(kwargs['keyboard_json']), external_user_id=str(external_user_id), text=str(text or ''))
        if kwargs.get('attachment'):
            params['attachment'] = kwargs['attachment']
        data = await self._vk_method('messages.send', params)
        return data.get('response', data)

    @staticmethod
    def _doc_attachment_from_save_response(data: dict[str, Any]) -> str:
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
        return 'audio_message' if suffix in {'.opus', '.ogg'} else 'doc'

    async def _ensure_doc_attachment(self, external_user_id: str, file_path: Path, *, media_type: str | None = None) -> str:
        upload_type = self._vk_upload_type_for_audio(file_path)
        cache_media_type = media_type or f'audio:{upload_type}'
        cached = get_cached_media_token('vk', file_path, media_type=cache_media_type)
        if cached is not None:
            return cached.remote_token
        upload_meta = await self._vk_method('docs.getMessagesUploadServer', {'peer_id': str(external_user_id), 'type': upload_type})
        upload_url = str((upload_meta.get('response') or {}).get('upload_url') or '').strip()
        if not upload_url:
            raise MessengerTransportError(f'Unexpected VK docs.getMessagesUploadServer response: {upload_meta}')
        uploaded = await asyncio.to_thread(multipart_upload, upload_url, field_name='file', path=file_path)
        uploaded_file = str(uploaded.get('file') or '').strip()
        if not uploaded_file:
            raise MessengerTransportError(f'Unexpected VK upload response for type={upload_type}: {uploaded}')
        saved = await self._vk_method('docs.save', {'file': uploaded_file, 'title': file_path.stem[:128], 'tags': 'metrotherapy,audio'})
        attachment = self._doc_attachment_from_save_response(saved)
        store_media_token('vk', file_path, attachment, media_type=cache_media_type)
        return attachment

    async def send_document_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        attachment = await self._ensure_doc_attachment(str(external_user_id), file_path, media_type=f'doc:{file_path.suffix.lower() or "file"}')
        return await self.send_text(external_user_id, caption or file_path.stem, attachment=attachment, **kwargs)

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        attachment = await self._ensure_doc_attachment(str(external_user_id), file_path)
        return await self.send_text(external_user_id, caption or f'🎧 Аудио: {file_path.stem}', attachment=attachment, **kwargs)
