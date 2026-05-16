from __future__ import annotations

import asyncio
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import settings
from runtime import messenger_max_ui as max_ui
from runtime.messenger_transport_errors import MessengerMediaNotReadyError, MessengerTransportError
from services.messenger.media_assets import get_cached_media_token, store_media_token
from services.messenger.provider_transport import json_request, multipart_upload


@dataclass
class MaxBotSender:
    token: str | None = None

    _main_menu_attachment = staticmethod(max_ui.main_menu_attachment)
    _demo_kind_attachment = staticmethod(max_ui.demo_kind_attachment)
    _score_scale_attachment = staticmethod(max_ui.score_scale_attachment)

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        token = (self.token or settings.MAX_BOT_TOKEN or "").strip()
        if not token:
            raise MessengerTransportError("MAX_BOT_TOKEN is empty")
        url = f"https://platform-api.max.ru/messages?user_id={urllib.parse.quote(str(external_user_id))}"
        attachments = list(kwargs.get("attachments") or max_ui.native_keyboard_attachments(str(text or "")))
        payload: dict[str, Any] = {"text": max_ui.prepare_text(text, has_native_keyboard=bool(attachments))}
        if attachments:
            payload["attachments"] = attachments
        if kwargs.get("disable_link_preview") is not None:
            url += f"&disable_link_preview={'true' if kwargs['disable_link_preview'] else 'false'}"
        if kwargs.get("format"):
            payload["format"] = kwargs["format"]
        if kwargs.get("notify") is not None:
            payload["notify"] = bool(kwargs["notify"])
        data = await asyncio.to_thread(json_request, url, method="POST", headers={"Authorization": token}, payload=payload)
        if isinstance(data, dict) and data.get("error"):
            raise MessengerTransportError(str(data["error"]))
        return data["message"] if isinstance(data, dict) and data.get("message") is not None else data

    async def _ensure_audio_token(self, file_path: Path) -> str:
        cached = get_cached_media_token("max", file_path, media_type="audio")
        if cached is not None:
            return cached.remote_token
        token = (self.token or settings.MAX_BOT_TOKEN or "").strip()
        if not token:
            raise MessengerTransportError("MAX_BOT_TOKEN is empty")
        upload_meta = await asyncio.to_thread(
            json_request,
            "https://platform-api.max.ru/uploads?type=audio",
            method="POST",
            headers={"Authorization": token},
            payload=None,
        )
        upload_url = str(upload_meta.get("url") or "").strip()
        if not upload_url:
            raise MessengerTransportError(f"Unexpected MAX upload response: {upload_meta}")

        uploaded = await asyncio.to_thread(multipart_upload, upload_url, token=token, field_name="data", path=file_path)
        media_token = ""
        if isinstance(uploaded, dict):
            media_token = str(uploaded.get("token") or uploaded.get("audio_token") or uploaded.get("file_token") or "").strip()
            payload = uploaded.get("payload")
            if not media_token and isinstance(payload, dict):
                media_token = str(payload.get("token") or "").strip()
        elif uploaded is not None:
            media_token = str(uploaded).strip()
        if not media_token:
            media_token = str(upload_meta.get("token") or "").strip()
        if not media_token:
            raise MessengerTransportError(f"Unexpected MAX audio upload result: meta={upload_meta!r}, uploaded={uploaded!r}")
        store_media_token("max", file_path, media_token, media_type="audio")
        return media_token

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        token = (self.token or settings.MAX_BOT_TOKEN or "").strip()
        if not token:
            raise MessengerTransportError("MAX_BOT_TOKEN is empty")
        media_token = await self._ensure_audio_token(file_path)
        url = f"https://platform-api.max.ru/messages?user_id={urllib.parse.quote(str(external_user_id))}"
        payload: dict[str, Any] = {"text": caption or "", "attachments": [{"type": "audio", "payload": {"token": media_token}}]}
        if kwargs.get("notify") is not None:
            payload["notify"] = bool(kwargs["notify"])
        delays = (0.0, 0.8, 1.6, 2.4)
        last_error: Exception | None = None
        for delay in delays:
            if delay:
                await asyncio.sleep(delay)
            try:
                data = await asyncio.to_thread(json_request, url, method="POST", headers={"Authorization": token}, payload=payload)
            except (OSError, ValueError, TypeError) as exc:  # pragma: no cover
                last_error = exc
                continue
            if isinstance(data, dict) and data.get("code") == "attachment.not.ready":
                last_error = MessengerMediaNotReadyError(str(data))
                continue
            if isinstance(data, dict) and data.get("error"):
                raise MessengerTransportError(str(data["error"]))
            return data.get("message", data)
        if last_error is not None:
            raise last_error if isinstance(last_error, MessengerTransportError) else MessengerTransportError(str(last_error))
        raise MessengerTransportError("MAX audio send failed without details")