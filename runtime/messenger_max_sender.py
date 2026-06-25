from __future__ import annotations

import asyncio
import os
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
    api_base_url: str | None = None

    _main_menu_attachment = staticmethod(max_ui.main_menu_attachment)
    _demo_kind_attachment = staticmethod(max_ui.demo_kind_attachment)
    _score_scale_attachment = staticmethod(max_ui.score_scale_attachment)

    def _token(self) -> str:
        token = (self.token or settings.MAX_BOT_TOKEN or "").strip()
        if not token:
            raise MessengerTransportError("MAX_BOT_TOKEN is empty")
        return token

    def _api_base(self) -> str:
        base = (
            self.api_base_url
            or os.getenv("MAX_API_BASE_URL")
            or getattr(settings, "MAX_API_BASE_URL", "")
            or "https://platform-api.max.ru"
        )
        clean = str(base or "").strip().rstrip("/")
        if not clean.startswith("https://"):
            raise MessengerTransportError("MAX_API_BASE_URL must start with https://")
        return clean

    @staticmethod
    def _upload_payload(upload_meta: dict[str, Any], uploaded: Any, *, media_type: str) -> dict[str, Any]:
        if isinstance(uploaded, dict):
            if uploaded.get("token"):
                return {"token": str(uploaded["token"])}
            payload = uploaded.get("payload")
            if isinstance(payload, dict) and payload.get("token"):
                return {"token": str(payload["token"])}
            for key in (f"{media_type}_token", "file_token"):
                if uploaded.get(key):
                    return {"token": str(uploaded[key])}
        elif uploaded is not None:
            value = str(uploaded).strip()
            if value:
                return {"token": value}
        if isinstance(upload_meta, dict) and upload_meta.get("token"):
            return {"token": str(upload_meta["token"])}
        raise MessengerTransportError(f"Unexpected MAX {media_type} upload result: meta={upload_meta!r}, uploaded={uploaded!r}")

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        token = self._token()
        url = f"{self._api_base()}/messages?user_id={urllib.parse.quote(str(external_user_id))}"
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

    async def _ensure_media_token(self, file_path: Path, *, media_type: str) -> str:
        cached = get_cached_media_token("max", file_path, media_type=media_type)
        if cached is not None:
            return cached.remote_token
        token = self._token()
        upload_meta = await asyncio.to_thread(
            json_request,
            f"{self._api_base()}/uploads?type={urllib.parse.quote(media_type)}",
            method="POST",
            headers={"Authorization": token},
            payload=None,
        )
        upload_url = str(upload_meta.get("url") or "").strip()
        if not upload_url:
            raise MessengerTransportError(f"Unexpected MAX {media_type} upload response: {upload_meta}")
        uploaded = await asyncio.to_thread(multipart_upload, upload_url, token=token, field_name="data", path=file_path)
        media_token = str(self._upload_payload(upload_meta, uploaded, media_type=media_type).get("token") or "").strip()
        if not media_token:
            raise MessengerTransportError(f"Unexpected MAX {media_type} upload result: meta={upload_meta!r}, uploaded={uploaded!r}")
        store_media_token("max", file_path, media_token, media_type=media_type)
        return media_token

    async def _send_media_payload(
        self,
        external_user_id: str,
        *,
        text: str,
        media_type: str,
        media_token: str,
        notify: bool | None = None,
    ) -> Any:
        token = self._token()
        url = f"{self._api_base()}/messages?user_id={urllib.parse.quote(str(external_user_id))}"
        payload: dict[str, Any] = {"text": text, "attachments": [{"type": media_type, "payload": {"token": media_token}}]}
        if notify is not None:
            payload["notify"] = bool(notify)
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
            return data.get("message", data) if isinstance(data, dict) else data
        if last_error is not None:
            raise last_error if isinstance(last_error, MessengerTransportError) else MessengerTransportError(str(last_error))
        raise MessengerTransportError(f"MAX {media_type} send failed without details")

    async def send_image_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        media_token = await self._ensure_media_token(file_path, media_type="image")
        return await self._send_media_payload(
            external_user_id,
            text=caption or "",
            media_type="image",
            media_token=media_token,
            notify=kwargs.get("notify"),
        )

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        media_token = await self._ensure_media_token(file_path, media_type="audio")
        return await self._send_media_payload(
            external_user_id,
            text=caption or "",
            media_type="audio",
            media_token=media_token,
            notify=kwargs.get("notify"),
        )
