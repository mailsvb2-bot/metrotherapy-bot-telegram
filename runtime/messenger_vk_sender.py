from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import settings
from runtime.messenger_transport_errors import MessengerTransportError
from runtime.messenger_vk_ui import prepare_vk_keyboard_json
from services.messenger.media_assets import get_cached_media_token, store_media_token
from services.messenger.provider_transport import form_request, multipart_upload

VK_MAX_BUTTONS_PER_ROW = 5
VK_MAX_BUTTON_ROWS = 6
VK_MAX_INLINE_CALLBACK_BUTTONS = 10


def _chunks(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _pack_keyboard_rows(rows: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    normalized: list[list[dict[str, Any]]] = []
    for row in rows:
        for chunk in _chunks(row, VK_MAX_BUTTONS_PER_ROW):
            if chunk:
                normalized.append(chunk)
    if len(normalized) <= VK_MAX_BUTTON_ROWS:
        return normalized
    flat = [button for row in normalized for button in row]
    repacked = _chunks(flat, VK_MAX_BUTTONS_PER_ROW)
    return repacked if len(repacked) <= VK_MAX_BUTTON_ROWS else normalized


def _button_count(rows: list[list[dict[str, Any]]]) -> int:
    return sum(len(row) for row in rows)



def _vk_upload_attempt_count() -> int:
    raw = (os.getenv("VK_AUDIO_UPLOAD_RETRIES") or os.getenv("MESSENGER_PROVIDER_RETRIES") or "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def _vk_upload_retry_backoff_sec(attempt: int) -> float:
    raw = (os.getenv("VK_AUDIO_UPLOAD_RETRY_BACKOFF_SEC") or os.getenv("MESSENGER_PROVIDER_RETRY_BACKOFF_SEC") or "0.5").strip()
    try:
        base = max(0.05, float(raw))
    except ValueError:
        base = 0.5
    return min(base * (2 ** max(0, attempt - 1)), 3.0)


def _vk_upload_response_is_retryable(data: dict[str, Any]) -> bool:
    error = str(data.get("error") or "").strip().casefold()
    error_descr = str(data.get("error_descr") or "").strip().casefold()
    if not error and not error_descr:
        return False
    return any(token in error or token in error_descr for token in ("unknown", "temporary", "timeout", "try_again", "internal"))


def _vk_audio_message_upload_path(file_path: Path) -> Path:
    if file_path.suffix.lower() != ".opus":
        return file_path
    tmp_dir = Path(tempfile.mkdtemp(prefix="vk-audio-message-"))
    upload_path = tmp_dir / f"{file_path.stem}.ogg"
    shutil.copyfile(file_path, upload_path)
    return upload_path


def _cleanup_vk_upload_path(upload_path: Path, source_path: Path) -> None:
    if upload_path == source_path:
        return
    try:
        tmp_dir = upload_path.parent
        upload_path.unlink(missing_ok=True)
        tmp_dir.rmdir()
    except OSError:
        pass




def _strip_raw_vk_payment_links(text: str) -> str:
    raw = str(text or "")
    head = raw.lstrip()
    if not (
        head.startswith("💳 Тарифы Метротерапии")
        or head.startswith("🎁 Подарить Метротерапию")
    ):
        return raw

    out: list[str] = []
    blank_count = 0
    for line in raw.splitlines():
        stripped = line.strip()
        if re.match(r"^https?://", stripped):
            continue
        if not stripped:
            blank_count += 1
            if blank_count > 1:
                continue
        else:
            blank_count = 0
        out.append(line)
    return "\n".join(out).strip()


def _strip_unsupported_vk_button_color(button: dict[str, Any]) -> dict[str, Any]:
    """Remove VK keyboard color from action types that do not support it.

    VK rejects open_link buttons with `color` using error_code=911. This guard
    keeps payment/audio fallback links deliverable even when upstream keyboard
    builders accidentally attach a color.
    """

    action = button.get("action")
    if isinstance(action, dict) and action.get("type") == "open_link":
        button.pop("color", None)
    return button


def _as_text_keyboard_json(keyboard: dict[str, Any], rows: list[list[dict[str, Any]]]) -> str:
    normalized_rows: list[list[dict[str, Any]]] = []
    for row in rows:
        normalized_row: list[dict[str, Any]] = []
        for button in row:
            normalized_button = dict(button)
            action = dict(normalized_button.get("action") or {})
            if action.get("type") == "callback":
                action["type"] = "text"
            normalized_button["action"] = action
            normalized_row.append(_strip_unsupported_vk_button_color(normalized_button))
        if normalized_row:
            normalized_rows.append(normalized_row)
    normalized = dict(keyboard)
    normalized["inline"] = False
    normalized["buttons"] = _pack_keyboard_rows(normalized_rows)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def _callback_keyboard_json(keyboard_json: str) -> str:
    try:
        keyboard = json.loads(str(keyboard_json or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return keyboard_json
    if not isinstance(keyboard, dict):
        return keyboard_json
    rows = keyboard.get("buttons")
    if not isinstance(rows, list):
        return keyboard_json

    normalized_rows: list[list[dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        normalized_row: list[dict[str, Any]] = []
        for button in row:
            if not isinstance(button, dict):
                continue
            normalized_button = dict(button)
            action = dict(normalized_button.get("action") or {})
            if action.get("type") == "text":
                action["type"] = "callback"
            normalized_button["action"] = action
            normalized_row.append(_strip_unsupported_vk_button_color(normalized_button))
        if normalized_row:
            normalized_rows.append(normalized_row)

    if _button_count(normalized_rows) > VK_MAX_INLINE_CALLBACK_BUTTONS:
        # VK rejects oversized inline callback keyboards with error_code=911.
        # Preserve full user functionality by sending the keyboard as regular
        # text buttons; VK includes payload in message_new, so routing still works.
        return _as_text_keyboard_json(keyboard, rows)

    normalized = dict(keyboard)
    normalized["inline"] = True
    normalized["buttons"] = _pack_keyboard_rows(normalized_rows)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


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

    async def answer_message_event(self, *, event_id: str, user_id: str, peer_id: str | None = None, text: str = "Открываю…") -> dict[str, Any]:
        clean_event_id = str(event_id or "").strip()
        clean_user_id = str(user_id or "").strip()
        if not clean_event_id or not clean_user_id:
            return {}
        event_data = json.dumps({"type": "show_snackbar", "text": str(text or "Открываю…")[:90]}, ensure_ascii=False)
        params = {
            "event_id": clean_event_id,
            "user_id": clean_user_id,
            "peer_id": str(peer_id or clean_user_id),
            "event_data": event_data,
        }
        return await self._vk_method("messages.sendMessageEventAnswer", params)

    async def send_text(self, external_user_id: str, text: str, **kwargs: Any):
        random_id = kwargs.get("random_id")
        if random_id is None:
            random_id = int(time.time_ns() % 2147483647)

        message_text = str(text or "")
        params = {"user_id": str(external_user_id), "random_id": int(random_id), "message": message_text}

        if kwargs.get("keyboard_json"):
            # Build VK buttons from the full original text first. Payment keyboards
            # need raw URLs here so they can become open_link buttons.
            keyboard_json = prepare_vk_keyboard_json(
                str(kwargs["keyboard_json"]),
                external_user_id=str(external_user_id),
                text=message_text,
            )
            params["keyboard"] = _callback_keyboard_json(keyboard_json)

            # After keyboard construction, remove raw checkout links from the
            # message body. The user sees clean tariff copy + VK buttons.
            message_text = _strip_raw_vk_payment_links(message_text)
            params["message"] = message_text

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
    def _photo_attachment_from_save_response(data: dict[str, Any]) -> str:
        response = data.get("response")
        candidates = response if isinstance(response, list) else [response]
        photo: dict[str, Any] | None = None
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if candidate.get("owner_id") is not None and candidate.get("id") is not None:
                photo = candidate
                break
        if not photo:
            raise MessengerTransportError(f"Unexpected VK photos.saveMessagesPhoto response: {data}")
        owner_id = photo.get("owner_id")
        photo_id = photo.get("id")
        access_key = str(photo.get("access_key") or "").strip()
        if owner_id is None or photo_id is None:
            raise MessengerTransportError(f"VK saved photo has no owner_id/id: {data}")
        attachment = f"photo{owner_id}_{photo_id}"
        if access_key:
            attachment += f"_{access_key}"
        return attachment

    @staticmethod
    def _vk_upload_type_for_audio(file_path: Path) -> str:
        return "audio_message" if file_path.suffix.lower() in {".opus", ".ogg"} else "doc"

    async def _upload_doc_attachment(self, external_user_id: str, file_path: Path, *, upload_type: str, cache_media_type: str) -> str:
        cached = get_cached_media_token("vk", file_path, media_type=cache_media_type)
        if cached is not None:
            return cached.remote_token

        attempts = _vk_upload_attempt_count() if upload_type == "audio_message" else 1
        last_uploaded: dict[str, Any] | None = None

        for attempt in range(1, attempts + 1):
            upload_meta = await self._vk_method("docs.getMessagesUploadServer", {"peer_id": str(external_user_id), "type": upload_type})
            upload_url = str((upload_meta.get("response") or {}).get("upload_url") or "").strip()
            if not upload_url:
                raise MessengerTransportError(f"Unexpected VK docs.getMessagesUploadServer response: {upload_meta}")

            upload_path = _vk_audio_message_upload_path(file_path) if upload_type == "audio_message" else file_path
            try:
                uploaded = await asyncio.to_thread(multipart_upload, upload_url, field_name="file", path=upload_path)
            finally:
                _cleanup_vk_upload_path(upload_path, file_path)

            uploaded_file = str(uploaded.get("file") or "").strip()
            if uploaded_file:
                break

            last_uploaded = uploaded
            if attempt < attempts and _vk_upload_response_is_retryable(uploaded):
                await asyncio.sleep(_vk_upload_retry_backoff_sec(attempt))
                continue

            raise MessengerTransportError(f"Unexpected VK upload response for type={upload_type}: {uploaded}")
        else:
            raise MessengerTransportError(f"Unexpected VK upload response for type={upload_type}: {last_uploaded}")

        saved = await self._vk_method("docs.save", {"file": uploaded_file, "title": file_path.stem[:128], "tags": "metrotherapy,audio"})
        attachment = self._doc_attachment_from_save_response(saved)
        store_media_token("vk", file_path, attachment, media_type=cache_media_type)
        return attachment

    async def _upload_photo_attachment(self, external_user_id: str, file_path: Path, *, cache_media_type: str = "image:photo") -> str:
        cached = get_cached_media_token("vk", file_path, media_type=cache_media_type)
        if cached is not None:
            return cached.remote_token

        upload_meta = await self._vk_method(
            "photos.getMessagesUploadServer",
            {"peer_id": str(external_user_id)},
        )
        upload_url = str((upload_meta.get("response") or {}).get("upload_url") or "").strip()
        if not upload_url:
            raise MessengerTransportError(f"Unexpected VK photos.getMessagesUploadServer response: {upload_meta}")

        uploaded = await asyncio.to_thread(
            multipart_upload,
            upload_url,
            field_name="photo",
            path=file_path,
        )

        server = uploaded.get("server")
        photo = uploaded.get("photo")
        upload_hash = uploaded.get("hash")
        if server is None or photo is None or upload_hash is None:
            raise MessengerTransportError(f"Unexpected VK photo upload response: {uploaded}")

        saved = await self._vk_method(
            "photos.saveMessagesPhoto",
            {
                "server": str(server),
                "photo": str(photo),
                "hash": str(upload_hash),
            },
        )
        attachment = self._photo_attachment_from_save_response(saved)
        store_media_token("vk", file_path, attachment, media_type=cache_media_type)
        return attachment

    async def _ensure_doc_attachment(self, external_user_id: str, file_path: Path, *, media_type: str | None = None) -> str:
        preferred_upload_type = self._vk_upload_type_for_audio(file_path)
        # VK accepts .opus/.ogg as audio_message. Falling back to type=doc for
        # these native audio files is both a UX regression and error masking: the
        # current group token may reject docs scope or VK may answer
        # wrong_music_file, hiding the real audio_message result from diagnostics.
        upload_types = [preferred_upload_type]

        last_error: MessengerTransportError | None = None
        for upload_type in upload_types:
            cache_media_type = media_type or f"audio:{upload_type}"
            try:
                return await self._upload_doc_attachment(
                    str(external_user_id),
                    file_path,
                    upload_type=upload_type,
                    cache_media_type=cache_media_type,
                )
            except MessengerTransportError as exc:
                last_error = exc
                raise
        if last_error is not None:
            raise last_error
        raise MessengerTransportError("VK upload failed before any upload attempt")

    async def send_image_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        attachment = await self._upload_photo_attachment(str(external_user_id), file_path)
        return await self.send_text(external_user_id, caption or file_path.stem, attachment=attachment, **kwargs)

    async def send_document_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        attachment = await self._ensure_doc_attachment(str(external_user_id), file_path, media_type=f"doc:{file_path.suffix.lower() or 'file'}")
        return await self.send_text(external_user_id, caption or file_path.stem, attachment=attachment, **kwargs)

    async def send_audio_file(self, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any):
        attachment = await self._ensure_doc_attachment(str(external_user_id), file_path)
        return await self.send_text(external_user_id, caption or f"🎧 Аудио: {file_path.stem}", attachment=attachment, **kwargs)
