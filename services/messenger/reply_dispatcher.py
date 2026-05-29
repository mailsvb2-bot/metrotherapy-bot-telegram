from __future__ import annotations

import asyncio
import logging
import urllib.parse
from pathlib import Path
from typing import Any

from config.settings import settings
from runtime.messenger_senders import MaxBotSender, VkBotSender, MessengerTransportError
from runtime.messenger_vk_ui import (
    vk_demo_kind_keyboard_json,
    vk_score_scale_keyboard_json,
    vk_weather_city_keyboard_json,
    vk_weather_keyboard_json,
    with_vk_keyboard,
)
from services.events import log_event
from services.messenger.audio_delivery import send_next_audio_to_user, _post_audio_control_kwargs
from services.messenger.outbound import SenderRegistry, UnsupportedMessengerDelivery
from services.messenger.package_payment_ui import gift_package_text, package_payment_text
from services.messenger.progress_charts import build_vk_mood_progress_chart_path
from services.messenger.provider_transport import json_request, multipart_upload
from services.messenger.text_ui import MessengerReply
from services.mood_text_flow import complete_pre_score_and_send, complete_post_score_and_send_next
from services.weather import get_weather_text_async, set_city

log = logging.getLogger(__name__)


def _vk_kwargs(platform: str, kwargs: dict[str, Any], canonical_user_id: int, text: str = "") -> dict[str, Any]:
    enriched = dict(kwargs)
    if text:
        enriched.setdefault("_text_for_keyboard", text)
    return with_vk_keyboard(platform, enriched, user_id=canonical_user_id)


def _looks_like_score_scale(text: str) -> bool:
    raw = str(text or "").casefold().replace("−", "-").replace("ё", "е")
    return "-10" in raw and "10" in raw and (
        "шкал" in raw
        or "оцен" in raw
        or "состояни" in raw
        or "после прослуш" in raw
    )


def _canonical_payment_text(platform: str, canonical_user_id: int, external_user_id: str, text: str) -> str:
    """Upgrade legacy VK/MAX payment texts to the canonical 4-package surface."""
    stripped = str(text or "").lstrip()
    if stripped.startswith("💳"):
        return package_payment_text(user_id=canonical_user_id, platform=platform, external_user_id=external_user_id)
    if stripped.startswith("🎁"):
        return gift_package_text(user_id=canonical_user_id, platform=platform, external_user_id=external_user_id)
    return text


def _max_upload_payload(upload_meta: dict[str, Any], uploaded: Any, *, media_type: str) -> dict[str, Any]:
    """Normalize MAX upload responses into message attachment payload.

    MAX upload endpoints can expose the token either in the initial upload meta,
    in the multipart upload response, or nested under ``payload``. Keep all
    provider-specific branching here so audio/chart delivery stays canonical.
    """
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


async def _send_max_image_file(external_user_id: str, file_path: Path, *, caption: str) -> Any:
    """Send a generated PNG chart to MAX as an image attachment."""
    token = (settings.MAX_BOT_TOKEN or "").strip()
    if not token:
        raise MessengerTransportError("MAX_BOT_TOKEN is empty")

    upload_meta = await asyncio.to_thread(
        json_request,
        "https://platform-api.max.ru/uploads?type=image",
        method="POST",
        headers={"Authorization": token},
        payload=None,
    )
    upload_url = str(upload_meta.get("url") or "").strip()
    if not upload_url:
        raise MessengerTransportError(f"Unexpected MAX image upload response: {upload_meta}")

    uploaded = await asyncio.to_thread(multipart_upload, upload_url, token=token, field_name="data", path=file_path)
    payload = _max_upload_payload(upload_meta, uploaded, media_type="image")

    url = f"https://platform-api.max.ru/messages?user_id={urllib.parse.quote(str(external_user_id))}"
    body = {"text": caption, "attachments": [{"type": "image", "payload": payload}]}

    delays = (0.0, 0.8, 1.6, 2.4)
    last_error: Exception | None = None
    for delay in delays:
        if delay:
            await asyncio.sleep(delay)
        try:
            data = await asyncio.to_thread(json_request, url, method="POST", headers={"Authorization": token}, payload=body)
        except (OSError, ValueError, TypeError) as exc:  # pragma: no cover
            last_error = exc
            continue
        if isinstance(data, dict) and data.get("code") == "attachment.not.ready":
            last_error = MessengerTransportError(str(data))
            continue
        if isinstance(data, dict) and data.get("error"):
            raise MessengerTransportError(str(data["error"]))
        return data.get("message", data) if isinstance(data, dict) else data

    if last_error is not None:
        raise last_error if isinstance(last_error, MessengerTransportError) else MessengerTransportError(str(last_error))
    raise MessengerTransportError("MAX image chart send failed without details")


async def _send_progress_chart_file(
    *,
    platform: str,
    sender: Any,
    external_user_id: str,
    chart_path: Path,
    caption: str,
    canonical_user_id: int,
) -> None:
    if platform == "max":
        await _send_max_image_file(external_user_id, chart_path, caption=caption)
        return

    if hasattr(sender, "send_document_file"):
        await sender.send_document_file(
            external_user_id,
            chart_path,
            caption=caption,
            **_vk_kwargs(platform, {}, canonical_user_id),
        )
        return

    await sender.send_audio_file(
        external_user_id,
        chart_path,
        caption=caption,
        **_vk_kwargs(platform, {}, canonical_user_id),
    )


async def _send_progress_chart_or_notice(
    *,
    platform: str,
    sender: Any,
    external_user_id: str,
    canonical_user_id: int,
) -> None:
    chart_path = build_vk_mood_progress_chart_path(canonical_user_id)
    if chart_path is None:
        await sender.send_text(
            external_user_id,
            "📈 Пока недостаточно данных для графика. Пройдите цикл: шкала ДО → аудио → Прослушал → шкала ПОСЛЕ.",
            **_vk_kwargs(platform, {}, canonical_user_id),
        )
        return
    try:
        await _send_progress_chart_file(
            platform=platform,
            sender=sender,
            external_user_id=external_user_id,
            chart_path=chart_path,
            caption="📈 Ваш график изменения состояния",
            canonical_user_id=canonical_user_id,
        )
        log.info("%s progress chart sent: user_id=%s path=%s", platform.upper(), canonical_user_id, chart_path)
    except Exception:  # validator: allow-wide-except
        log.exception("%s progress chart send failed", platform.upper())
        await sender.send_text(
            external_user_id,
            "⚠️ График построен, но не удалось отправить его в этот мессенджер.",
            **_vk_kwargs(platform, {}, canonical_user_id),
        )


async def send_reply_bundle(
    platform: str,
    external_user_id: str,
    canonical_user_id: int,
    replies: list[MessengerReply],
) -> None:
    registry = SenderRegistry(max=MaxBotSender(), vk=VkBotSender())
    sender = registry.get(platform)
    if sender is None:
        raise MessengerTransportError(f"No sender for {platform}")

    for reply in replies:
        if reply.kind == "text":
            text = _canonical_payment_text(platform, canonical_user_id, external_user_id, reply.text)
            if not str(text or "").strip():
                continue
            kwargs: dict[str, Any] = {}
            if platform == "vk":
                keyboard_kind = (reply.meta or {}).get("vk_keyboard")
                if keyboard_kind == "demo_kind":
                    kwargs["keyboard_json"] = vk_demo_kind_keyboard_json()
                elif keyboard_kind == "score_scale" or _looks_like_score_scale(text):
                    kwargs["keyboard_json"] = vk_score_scale_keyboard_json()
                elif keyboard_kind == "weather":
                    kwargs["keyboard_json"] = vk_weather_keyboard_json()
                elif keyboard_kind == "weather_city":
                    kwargs["keyboard_json"] = vk_weather_city_keyboard_json()
            await sender.send_text(external_user_id, text, **_vk_kwargs(platform, kwargs, canonical_user_id, text=text))
            continue

        if reply.kind == "next_audio":
            try:
                result = await send_next_audio_to_user(
                    canonical_user_id,
                    senders=registry,
                    target_platform=platform,
                    fallback=platform,
                )
                log.info(
                    "%s next_audio delivery result: user_id=%s transport=%s item=%s",
                    platform.upper(),
                    canonical_user_id,
                    result.transport,
                    getattr(getattr(result, "item", None), "anchor", None),
                )
                if result.transport == "none":
                    await sender.send_text(external_user_id, result.message, **_vk_kwargs(platform, {}, canonical_user_id))
            except (MessengerTransportError, UnsupportedMessengerDelivery, OSError):
                log.exception("%s cross-channel audio delivery failed", platform.upper())
                await sender.send_text(
                    external_user_id,
                    "⚠️ Не удалось отправить следующее аудио в этот мессенджер. "
                    "Для MAX/ВКонтакте нужен публичный адрес MESSENGER_PUBLIC_BASE_URL, "
                    "чтобы бот мог присылать безопасную ссылку на следующий файл.",
                    **_vk_kwargs(platform, {}, canonical_user_id),
                )
            continue

        if reply.kind == "weather_show":
            txt = await get_weather_text_async(canonical_user_id, timeout_sec=2.0)
            await sender.send_text(
                external_user_id,
                txt + "\n\nМожно нажать «🏙 Изменить город» или отправить команду: город.",
                **_vk_kwargs(platform, {"keyboard_json": vk_weather_keyboard_json()} if platform == "vk" else {}, canonical_user_id),
            )
            continue

        if reply.kind == "weather_set_city":
            city = (reply.meta or {}).get("city", "").strip()
            txt = await asyncio.to_thread(set_city, canonical_user_id, city)
            await sender.send_text(
                external_user_id,
                txt,
                **_vk_kwargs(platform, {"keyboard_json": vk_weather_keyboard_json()} if platform == "vk" else {}, canonical_user_id),
            )
            continue

        if reply.kind == "progress_chart":
            await _send_progress_chart_or_notice(
                platform=platform,
                sender=sender,
                external_user_id=external_user_id,
                canonical_user_id=canonical_user_id,
            )
            continue

        if reply.kind == "pre_score_result":
            score = int((reply.meta or {}).get("score", "0") or 0)
            session_id = int((reply.meta or {}).get("session_id", "0") or 0)
            try:
                await complete_pre_score_and_send(canonical_user_id, score, session_id=session_id, target_platform=platform)
            except Exception:  # validator: allow-wide-except
                log.exception("%s pre-score flow failed", platform.upper())
                await sender.send_text(
                    external_user_id,
                    "⚠️ Оценку сохранил, но не смог отправить аудио. Напишите: continue",
                    **_vk_kwargs(platform, {}, canonical_user_id),
                )
            continue

        if reply.kind == "post_score_result":
            score = int((reply.meta or {}).get("score", "0") or 0)
            session_id = int((reply.meta or {}).get("session_id", "0") or 0)
            try:
                await complete_post_score_and_send_next(canonical_user_id, score, session_id=session_id, target_platform=platform)
            except Exception:  # validator: allow-wide-except
                log.exception("%s post-score flow failed", platform.upper())
                await sender.send_text(
                    external_user_id,
                    "⚠️ Оценку после прослушивания сохранил, но не смог отправить следующее аудио. Напишите: continue",
                    **_vk_kwargs(platform, {}, canonical_user_id),
                )
            continue

        if reply.kind == "audio_confirmed_next":
            result = confirm_pending_audio_delivery(canonical_user_id, platform=platform)
            await sender.send_text(external_user_id, result.message, **_vk_kwargs(platform, {}, canonical_user_id))
            if result.next_audio_ready:
                try:
                    sent = await send_next_audio_to_user(
                        canonical_user_id,
                        senders=registry,
                        target_platform=platform,
                        fallback=platform,
                    )
                    if sent.transport == "none":
                        await sender.send_text(external_user_id, sent.message, **_vk_kwargs(platform, {}, canonical_user_id))
                except (MessengerTransportError, UnsupportedMessengerDelivery, OSError):
                    log.exception("%s next audio after confirm failed", platform.upper())
                    await sender.send_text(
                        external_user_id,
                        "⚠️ Подтверждение сохранено, но следующее аудио не отправилось. Напишите: continue",
                        **_vk_kwargs(platform, {}, canonical_user_id),
                    )
            continue

        log_event(canonical_user_id, f"{platform}_unsupported_reply_kind", {"kind": reply.kind})
