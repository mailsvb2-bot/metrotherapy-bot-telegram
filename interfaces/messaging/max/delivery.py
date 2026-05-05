from __future__ import annotations

"""Deliver CanonicalResponse through the current MAX sender.

This is the safe bridge between the new unified renderer and the existing async
runtime sender. It keeps MAX-specific rendering outside business logic while
avoiding a risky full rewrite of runtime/messenger_senders.py.
"""

import asyncio
from pathlib import Path
from typing import Any

from interfaces.messaging.contracts import CanonicalResponse
from interfaces.messaging.max.renderer import render_max_response
from interfaces.messaging.observability import observe


def _first_keyboard_attachment(payload: dict[str, Any]) -> dict[str, Any] | None:
    attachments = payload.get("attachments") or []
    if not isinstance(attachments, list):
        return None
    for attachment in attachments:
        if isinstance(attachment, dict) and attachment.get("type") == "inline_keyboard":
            return attachment
    return None


def _post_score_keyboard() -> dict[str, Any]:
    return {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [
                [
                    {
                        "type": "message",
                        "text": "📈 Посмотреть график изменения состояния",
                        "payload": "progress",
                    }
                ],
                [
                    {"type": "message", "text": "🎧 Другая практика", "payload": "demo"},
                    {"type": "message", "text": "🔐 Открыть полный маршрут", "payload": "full"},
                ],
                [{"type": "message", "text": "🏠 Меню", "payload": "start"}],
            ]
        },
    }


def _is_post_score_response(text: str) -> bool:
    return (text or "").lstrip().startswith("✅ Оценку после прослушивания")


def _pick_media_token(data: dict[str, Any]) -> str:
    """Extract a reusable MAX media token from known upload response shapes."""
    for key in ("token", "media_token", "file_token"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    payload = data.get("payload")
    if isinstance(payload, dict):
        nested = _pick_media_token(payload)
        if nested:
            return nested

    photos = data.get("photos")
    if isinstance(photos, dict):
        nested = _pick_media_token(photos)
        if nested:
            return nested

    attachment = data.get("attachment")
    if isinstance(attachment, dict):
        nested = _pick_media_token(attachment)
        if nested:
            return nested

    return ""


async def _send_max_image_file(sender: Any, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any) -> Any:
    """Send PNG/JPG progress charts through MAX image upload, not audio upload."""
    from config.settings import settings
    from runtime.messenger_senders import _json_request, _max_multipart_upload, MessengerTransportError

    token = (getattr(sender, "token", None) or settings.MAX_BOT_TOKEN or "").strip()
    if not token:
        raise MessengerTransportError("MAX_BOT_TOKEN is empty")

    api_base = sender._api_base_url()
    upload_meta = await asyncio.to_thread(
        _json_request,
        f"{api_base}/uploads?type=image",
        method="POST",
        headers={"Authorization": token},
        payload=None,
    )
    upload_url = str(upload_meta.get("url") or "").strip()
    if not upload_url:
        raise MessengerTransportError(f"Unexpected MAX image upload response: {upload_meta}")

    uploaded = await asyncio.to_thread(
        _max_multipart_upload,
        upload_url,
        token=token,
        field_name="data",
        path=file_path,
    )
    media_token = _pick_media_token(uploaded) or _pick_media_token(upload_meta)
    if not media_token:
        raise MessengerTransportError(
            f"MAX image upload completed but no media token was returned: upload_meta={upload_meta}, uploaded={uploaded}"
        )

    payload: dict[str, Any] = {
        "text": caption or "",
        "attachments": [{"type": "image", "payload": {"token": media_token}}],
    }
    if kwargs.get("notify") is not None:
        payload["notify"] = bool(kwargs["notify"])

    url = f"{api_base}/messages?user_id={external_user_id}"
    data = await asyncio.to_thread(
        _json_request,
        url,
        method="POST",
        headers={"Authorization": token},
        payload=payload,
    )
    if isinstance(data, dict) and data.get("error"):
        raise MessengerTransportError(str(data["error"]))
    return data.get("message", data) if isinstance(data, dict) else data


def _is_image_path(file_path: Any) -> bool:
    suffix = Path(file_path).suffix.lower()
    return suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".heic"}


def _enable_direct_sender_image_bridge() -> None:
    """Route MAX PNG/JPG chart sends to the image attachment API.

    The legacy progress_chart path calls sender.send_audio_file(chart.png).
    For MAX this must not hit /uploads?type=audio. This bridge preserves the
    working audio path and only redirects image file suffixes to /uploads?type=image.
    """
    try:
        from runtime.messenger_senders import MaxBotSender
    except Exception:
        return

    marker = "_direct_image_file_bridge_enabled"
    if bool(getattr(MaxBotSender, marker, False)):
        return

    base_send_audio_file = MaxBotSender.send_audio_file

    async def send_audio_or_image_file(self: Any, external_user_id: str, file_path: Path, *, caption: str | None = None, **kwargs: Any) -> Any:
        path = Path(file_path)
        if _is_image_path(path):
            return await _send_max_image_file(self, external_user_id, path, caption=caption, **kwargs)
        return await base_send_audio_file(self, external_user_id, path, caption=caption, **kwargs)

    MaxBotSender.send_audio_file = send_audio_or_image_file
    setattr(MaxBotSender, marker, True)


def _enable_direct_sender_post_score_keyboard() -> None:
    """Attach the chart keyboard to direct MaxBotSender text responses too.

    runtime.messenger_webhooks still sends the auto_post_score result directly
    through MaxBotSender.send_text. This keeps that legacy path visually aligned
    with the canonical MAX renderer without changing business flow semantics.
    """
    try:
        from runtime.messenger_senders import MaxBotSender
    except Exception:
        return

    marker = "_direct_post_score_keyboard_enabled"
    if bool(getattr(MaxBotSender, marker, False)):
        return

    base_keyboard_for_text = MaxBotSender._keyboard_for_text

    def keyboard_for_text(cls: type[Any], text: str, *, external_user_id: str) -> dict[str, Any] | None:
        if _is_post_score_response(str(text or "")):
            return _post_score_keyboard()
        return base_keyboard_for_text(str(text or ""), external_user_id=str(external_user_id))

    MaxBotSender._keyboard_for_text = classmethod(keyboard_for_text)
    setattr(MaxBotSender, marker, True)


_enable_direct_sender_post_score_keyboard()
_enable_direct_sender_image_bridge()


async def send_canonical_max_response(sender: Any, external_user_id: str, response: CanonicalResponse) -> Any:
    rendered = render_max_response(response)
    keyboard = _first_keyboard_attachment(rendered.payload)
    if keyboard is None and _is_post_score_response(rendered.text):
        keyboard = _post_score_keyboard()
    try:
        result = await sender.send_text(
            external_user_id,
            rendered.text,
            max_keyboard=keyboard,
        )
    except Exception as exc:
        observe(
            "max",
            "delivery",
            "error",
            has_buttons=bool(keyboard),
            error_type=type(exc).__name__,
        )
        raise
    observe("max", "delivery", "ok", has_buttons=bool(keyboard))
    return result
