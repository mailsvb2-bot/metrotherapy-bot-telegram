from __future__ import annotations

import asyncio
import logging
from typing import Any

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
from services.messenger.progress_charts import build_vk_mood_progress_chart_path
from services.messenger.text_ui import MessengerReply
from services.mood_text_flow import complete_pre_score_and_send, complete_post_score_and_send_next
from services.weather import get_weather_text_async, set_city

log = logging.getLogger(__name__)


def _vk_kwargs(platform: str, kwargs: dict[str, Any], canonical_user_id: int) -> dict[str, Any]:
    return with_vk_keyboard(platform, kwargs, user_id=canonical_user_id)


async def send_reply_bundle(
    platform: str,
    external_user_id: str,
    canonical_user_id: int,
    replies: list[MessengerReply],
) -> None:
    """Dispatch canonical messenger replies to a concrete messenger sender.

    Runtime webhook modules should only normalize ingress and call this service;
    reply semantics, fallback wording and cross-channel delivery live here.
    """
    registry = SenderRegistry(max=MaxBotSender(), vk=VkBotSender())
    sender = registry.get(platform)
    if sender is None:
        raise MessengerTransportError(f"No sender for {platform}")

    for reply in replies:
        if reply.kind == "text":
            kwargs: dict[str, Any] = {}
            if platform == "vk":
                keyboard_kind = (reply.meta or {}).get("vk_keyboard")
                if keyboard_kind == "demo_kind":
                    kwargs["keyboard_json"] = vk_demo_kind_keyboard_json()
                elif keyboard_kind == "score_scale":
                    kwargs["keyboard_json"] = vk_score_scale_keyboard_json()
                elif keyboard_kind == "weather":
                    kwargs["keyboard_json"] = vk_weather_keyboard_json()
                elif keyboard_kind == "weather_city":
                    kwargs["keyboard_json"] = vk_weather_city_keyboard_json()
            await sender.send_text(external_user_id, reply.text, **_vk_kwargs(platform, kwargs, canonical_user_id))
            continue

        if reply.kind == "next_audio":
            try:
                result = await send_next_audio_to_user(
                    canonical_user_id,
                    senders=registry,
                    target_platform=platform,
                    fallback=platform,
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
            if not city:
                await sender.send_text(
                    external_user_id,
                    "Пожалуйста, напишите название города текстом.",
                    **_vk_kwargs(platform, {"keyboard_json": vk_weather_city_keyboard_json()} if platform == "vk" else {}, canonical_user_id),
                )
                continue

            ok, info = await asyncio.to_thread(set_city, canonical_user_id, city)
            if not ok:
                await sender.send_text(
                    external_user_id,
                    "❌ " + str(info),
                    **_vk_kwargs(platform, {"keyboard_json": vk_weather_city_keyboard_json()} if platform == "vk" else {}, canonical_user_id),
                )
                continue

            log_event(canonical_user_id, "weather_city_set", {"city": str(info), "platform": platform})
            txt = await get_weather_text_async(canonical_user_id, timeout_sec=2.0)
            await sender.send_text(
                external_user_id,
                f"✅ Город принят: {info}.\n\n{txt}",
                **_vk_kwargs(platform, {"keyboard_json": vk_weather_keyboard_json()} if platform == "vk" else {}, canonical_user_id),
            )
            continue

        if reply.kind == "progress_chart":
            chart_path = build_vk_mood_progress_chart_path(canonical_user_id)
            if chart_path is None:
                await sender.send_text(
                    external_user_id,
                    "📈 Пока недостаточно данных для графика. Пройдите цикл: шкала ДО → аудио → Прослушал → шкала ПОСЛЕ.",
                    **_vk_kwargs(platform, {}, canonical_user_id),
                )
                continue

            try:
                await sender.send_audio_file(
                    external_user_id,
                    chart_path,
                    caption="📈 Ваш график прогресса Метротерапии",
                    **_vk_kwargs(platform, {}, canonical_user_id),
                )
                log.info("%s progress chart sent: user_id=%s path=%s", platform.upper(), canonical_user_id, chart_path)
            except Exception:  # validator: allow-wide-except
                log.exception("%s progress chart send failed", platform.upper())
                await sender.send_text(
                    external_user_id,
                    "⚠️ График построен, но не удалось отправить его во ВКонтакте.",
                    **_vk_kwargs(platform, {}, canonical_user_id),
                )
            continue

        if reply.kind == "auto_pre_score":
            result = await complete_pre_score_and_send(
                canonical_user_id,
                platform=platform,
                score=int(reply.meta.get("score") or "0"),
                senders=registry,
            )
            kwargs: dict[str, Any] = {}
            if platform == "vk" and getattr(result, "prompt_done", False):
                kwargs.update(_post_audio_control_kwargs("vk"))
            await sender.send_text(external_user_id, result.message, **_vk_kwargs(platform, kwargs, canonical_user_id))
            continue

        if reply.kind == "auto_post_score":
            result = await complete_post_score_and_send_next(
                canonical_user_id,
                platform=platform,
                score=int(reply.meta.get("score") or "0"),
                senders=registry,
            )
            await sender.send_text(external_user_id, result.message, **_vk_kwargs(platform, {}, canonical_user_id))
            continue
