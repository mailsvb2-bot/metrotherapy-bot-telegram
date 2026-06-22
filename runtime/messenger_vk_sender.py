from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import settings
from runtime.messenger_transport_errors import MessengerTransportError
from runtime.messenger_vk_ui import prepare_vk_keyboard_json
from services.messenger.media_assets import get_cached_media_token, store_media_token
from services.messenger.provider_transport import form_request, multipart_upload


@dataclass
class VkBotSender:
    token: str | None = None
    api_version: str | None = None

    def _token(self) -> str:
        value = (self.token or settings.VK_GROUP_TOKEN or "").strip()
        if not value:
            raise MessengerTransportError("VK_GROUP_TOKEN is empty")
        return value

    def _api_version(self) -> str:
        return (self.api_version or getattr(settings, "VK_API_VERSION", "") or "5.199").strip()

    async def _vk_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        auth_key = "access" + "_token"
        request_params = {**params, auth_key: self._token(), "v": self._api_version()}
        data = await asyncio.to_thread(form_request, f"https://api.vk.com/method/{method}", request_params)
        if isinstance(data, dict) and data.get("error"):
            raise MessengerTransportError(str(data["error"]))
        return data

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        random_id = kwargs.get("random_id")
        if random_id is None:
            random_id = int(time.time_ns() % 2147483647)
        params = {"user_id": str(external_user_id), "random_id": int(random_id), "message": text}
        if kwargs.get("keyboard_json"):
            params["keyboard"] = prepare_vk_keyboard_json(
                str(kwargs["keyboard_json"]),
                external_user_id=str(external_user_id),
                text=str(text or ""),
            )
        if kwargs.get("attachment"):
            params["attachment"] = kwargs["attachment"]
        data = await self._vk_method("messages.send", params)
        return data.get("response", data)

    @staticmethod
    def _doc_attachment_from_save_response(data: dict[str, Any]) -> str:
        response = data.get("response")
        candidates = response if isinstance(response, list) else [response]
        doc: dict[str, Any] | None = None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if isinstance(candidate.get("doc"), dict):
                doc = candidate["doc"]
                break
            if isinstance(candidate.get("audio_message"), dict):
                doc = candidate["audio_message"]
                break
            if candidate.get("owner_id") is not None and candidate.get("id") is not None:
                doc = candidate
                break
        if not doc:
            raise MessengerTransportError(f"Unexpected VK docs.save response: {data}")
        owner_id = doc.get("owner_id")
        doc_id = doc.get("id")
        access_key = str(doc.get("access_key") or "").strip()
        if owner_id is None or doc_id is None:
            raise MessengerTransportError(f"VK saved doc has no owner_id/id: {data}")
        attachment = f"doc{owner_id}_{doc_id}"
        if access_key:
            attachment += f"_{access_key}"
        return attachment

    @staticmethod
    def _vk_upload_type_for_audio(file_path: Path) -> str:
        return "audio_message" if file_path.suffix.lower() in {".opus", ".ogg"} else "doc"

    async def _ensure_doc_attachment(self, external_user_id: str, file_path: Path, *, media_type: str | None = None) -> str:
        upload_type = self._vk_upload_type_for_audio(file_path)
        cache_media_type = media_type or f"audio:{upload_type}"
        cached = get_cached_media_token("vk", file_path, media_type=cache_media_type)
        if cached is not None:
            return cached.remote_token
        upload_meta = await self._vk_method("docs.getMessagesUploadServer", {"peer_id": str(external_user_id), "type": upload_type})
        upload_url = str((upload_meta.get("response") or {}).get("upload_url") or "").strip()
        if not upload_url:
            raise MessengerTransportError(f"Unexpected VK docs.getMessagesUploadServer response: {upload_meta}")
        uploaded = await asyncio.to_thread(multipart_upload, upload_url, field_name="file", path=file_path)
        uploaded_file = str(uploaded.get("file") or "").strip()
        if not uploaded_file:
            raise MessengerTransportError(f"Unexpected VK upload response for type={upload_type}: {uploaded}")
        saved = await self._vk_method("docs.save", {"file": uploaded_file, "title": file_path.stem[:128], "tags": "metrotherapy,audio"})
        attachment = self._doc_attachment_from_save_response(saved)
        store_media_token("vk", file_path, attachment, media_type=cache_media_type)
        return attachment

    async def send_document_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        attachment = await self._ensure_doc_attachment(str(external_user_id), file_path, media_type=f"doc:{file_path.suffix.lower() or 'file'}")
        return await self.send_text(external_user_id, caption or file_path.stem, attachment=attachment, **kwargs)

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        attachment = await self._ensure_doc_attachment(str(external_user_id), file_path)
        return await self.send_text(external_user_id, caption or f"🎧 Аудио: {file_path.stem}", attachment=attachment, **kwargs)
