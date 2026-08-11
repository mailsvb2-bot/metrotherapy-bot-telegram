from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

from services.admin import (
    can_use_scoped_admin_permission,
    is_superadmin,
    staff_roles,
)
from services.admin_permissions import VISUAL_CREATIVE_PERMISSION
from services.metrotherapy_creative_studio import (
    build_metrotherapy_studio_variants,
    submit_metrotherapy_studio_variant,
)
from services.metrotherapy_visual_creatives import (
    create_metrotherapy_visual,
    materialize_metrotherapy_visual,
    poll_metrotherapy_visual,
    visual_wait_seconds,
)
from services.visual_creative_capability import (
    visual_creative_country_code,
    visual_creative_enabled,
    visual_creative_studio_enabled,
)
from services.visual_creative_gateway import (
    VisualCreativeGatewayError,
    download_render_asset,
)

router = Router()
log = logging.getLogger(__name__)

_VISUAL_CREATIVE_ROLES = frozenset({"admin", "marketing"})
_JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_STUDIO_LABELS = {
    "night_city": "Ночной город и отражения",
    "nature_breath": "Природа, воздух и пространство",
    "warm_human": "Тёплая человеческая сцена",
}


def _uid(message: Message) -> int | None:
    return int(message.from_user.id) if message.from_user is not None else None


def _chat_id(message: Message) -> int:
    return int(message.chat.id)


def _command_payload(message: Message) -> str:
    text = str(message.text or "")
    return text.split(maxsplit=1)[1].strip() if " " in text else ""


def _can_use_visual_creatives(user_id: int) -> bool:
    """Authorize the enabled marketing capability without widening staff access."""

    if not visual_creative_enabled():
        return False
    uid = int(user_id)
    if is_superadmin(uid):
        return True
    if not (staff_roles(uid) & _VISUAL_CREATIVE_ROLES):
        return False
    return can_use_scoped_admin_permission(uid, VISUAL_CREATIVE_PERMISSION)


def _can_use_creative_studio(user_id: int) -> bool:
    return bool(visual_creative_studio_enabled() and _can_use_visual_creatives(user_id))


def _parse_pack_payload(raw: str) -> tuple[str, int, str]:
    parts = str(raw or "").strip().split(maxsplit=2)
    if len(parts) != 3:
        raise ValueError("creative_pack_payload_required")
    kind = parts[0].strip().lower()
    if kind not in {"image", "video"}:
        raise ValueError("creative_pack_kind_invalid")
    try:
        index = int(parts[1])
    except ValueError as exc:
        raise ValueError("creative_pack_variant_invalid") from exc
    if index not in {1, 2, 3}:
        raise ValueError("creative_pack_variant_invalid")
    concept = " ".join(parts[2].split())
    if not concept:
        raise ValueError("creative_pack_concept_required")
    return kind, index, concept


def _idempotency_key(message: Message, *, user_id: int, kind: str) -> str:
    """Build a stable request key unique across Telegram chats.

    Telegram message identifiers are unique only within a chat. Including the
    chat id prevents two different staff conversations from colliding when they
    happen to reuse the same message id.
    """

    seed = f"{int(user_id)}|{_chat_id(message)}|{int(message.message_id)}|{str(kind)}"
    return "metro:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _remove_local_materialization(path: Path) -> None:
    """Remove the Telegram-side copy after upload; the gateway owns durable media."""

    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Unable to remove materialized visual creative", extra={"path": str(path)})


async def _send_job(message: Message, job) -> None:
    if job.status == "succeeded" and job.asset_ready:
        try:
            path = await asyncio.to_thread(materialize_metrotherapy_visual, job)
        except VisualCreativeGatewayError:
            log.exception(
                "Visual creative materialization failed",
                extra={"job_id": getattr(job, "id", ""), "kind": getattr(job, "kind", "")},
            )
            await message.answer("Креатив создан, но файл безопасно получить не удалось.")
            return

        caption = f"Готово · {job.provider} · {job.model or 'default'}"
        try:
            if job.kind == "video":
                await message.answer_video(FSInputFile(path), caption=caption)
            else:
                await message.answer_photo(FSInputFile(path), caption=caption)
        finally:
            await asyncio.to_thread(_remove_local_materialization, path)
        return

    if job.status in {"queued", "running"}:
        await message.answer("Креатив ещё генерируется. Проверьте: " + f"/creative_status {job.id}")
        return

    await message.answer(f"Не удалось создать креатив: {job.error_code or 'provider_failed'}")


async def _send_render_pack(message: Message, pack, *, variant_id: str) -> None:
    preferred = ("story", "feed", "square", "landscape")
    ready = {
        str(asset.format_id): asset
        for asset in tuple(getattr(pack, "assets", ()) or ())
        if bool(getattr(asset, "asset_ready", False))
    }
    selected = next((name for name in preferred if name in ready), None)
    if selected is None:
        await message.answer("Render pack создан, но готового preview-файла нет.")
        return

    try:
        path = await asyncio.to_thread(download_render_asset, pack, selected)
    except VisualCreativeGatewayError:
        log.exception(
            "Creative studio render materialization failed",
            extra={"pack_id": getattr(pack, "id", ""), "variant_id": variant_id},
        )
        await message.answer("Вариант создан, но preview-файл безопасно получить не удалось.")
        return

    try:
        caption = f"Creative Studio · {selected} · {variant_id}"
        asset = ready[selected]
        if str(getattr(asset, "kind", "")) == "video":
            await message.answer_video(FSInputFile(path), caption=caption)
        else:
            await message.answer_photo(FSInputFile(path), caption=caption)
    finally:
        await asyncio.to_thread(_remove_local_materialization, path)


async def _generate(message: Message, *, kind: str) -> None:
    uid = _uid(message)
    if uid is None or not _can_use_visual_creatives(uid):
        await message.answer("Недоступно.")
        return

    concept = _command_payload(message)
    if not concept:
        await message.answer(f"Использование: /creative_{kind} <описание рекламного визуала>")
        return

    try:
        job = await asyncio.to_thread(
            create_metrotherapy_visual,
            concept=concept,
            kind=kind,
            scope_id=f"staff:{uid}",
            idempotency_key=_idempotency_key(message, user_id=uid, kind=kind),
            country_code=visual_creative_country_code(),
            wait_seconds=visual_wait_seconds(),
        )
    except VisualCreativeGatewayError:
        log.exception("Visual creative submission failed", extra={"staff_user_id": uid, "kind": kind})
        await message.answer("Не удалось запустить генерацию. Проверьте Visual Creative Gateway.")
        return

    await _send_job(message, job)


@router.message(Command("creative_image"))
async def creative_image(message: Message) -> None:
    await _generate(message, kind="image")


@router.message(Command("creative_video"))
async def creative_video(message: Message) -> None:
    await _generate(message, kind="video")


@router.message(Command("creative_status"))
async def creative_status(message: Message) -> None:
    uid = _uid(message)
    if uid is None or not _can_use_visual_creatives(uid):
        await message.answer("Недоступно.")
        return

    job_id = _command_payload(message)
    if not _JOB_ID_RE.fullmatch(job_id):
        await message.answer("Использование: /creative_status <job_id>")
        return

    try:
        job = await asyncio.to_thread(
            poll_metrotherapy_visual,
            job_id=job_id,
            scope_id=f"staff:{uid}",
        )
    except VisualCreativeGatewayError:
        log.exception("Visual creative status check failed", extra={"staff_user_id": uid})
        await message.answer("Не удалось проверить статус креатива.")
        return

    await _send_job(message, job)


@router.message(Command("creative_concepts"))
async def creative_concepts(message: Message) -> None:
    uid = _uid(message)
    if uid is None or not _can_use_creative_studio(uid):
        await message.answer("Недоступно.")
        return

    concept = _command_payload(message)
    if not concept:
        await message.answer("Использование: /creative_concepts <идея рекламного материала>")
        return

    try:
        variants = build_metrotherapy_studio_variants(
            concept,
            kind="image",
            country_code=visual_creative_country_code(),
        )
    except ValueError:
        await message.answer("Не удалось безопасно подготовить концепции.")
        return

    lines = ["Creative Studio подготовил 3 направления без платной AI-генерации:"]
    for index, variant in enumerate(variants, start=1):
        label = _STUDIO_LABELS.get(str(variant.angle_id), str(variant.angle_id))
        lines.append(f"{index}. {label}")
    lines.extend(
        [
            "",
            "Запустить выбранный вариант:",
            "/creative_pack image 1 <та же идея>",
            "или /creative_pack video 1 <та же идея>",
            "",
            "AI вызывается только после выбора варианта.",
        ]
    )
    await message.answer("\n".join(lines))


@router.message(Command("creative_pack"))
async def creative_pack(message: Message) -> None:
    uid = _uid(message)
    if uid is None or not _can_use_creative_studio(uid):
        await message.answer("Недоступно.")
        return

    try:
        kind, index, concept = _parse_pack_payload(_command_payload(message))
        variants = build_metrotherapy_studio_variants(
            concept,
            kind=kind,
            country_code=visual_creative_country_code(),
        )
        variant = variants[index - 1]
        job, pack = await asyncio.to_thread(
            submit_metrotherapy_studio_variant,
            variant,
            staff_user_id=uid,
            wait_seconds=visual_wait_seconds(),
        )
    except (IndexError, TypeError, ValueError):
        await message.answer(
            "Использование: /creative_pack image|video 1|2|3 <идея>. "
            "Опасные лечебные/принуждающие обещания блокируются до AI-вызова."
        )
        return
    except VisualCreativeGatewayError:
        log.exception("Creative studio submission failed", extra={"staff_user_id": uid})
        await message.answer("Не удалось запустить Creative Studio. Проверьте Visual Creative Gateway.")
        return

    if pack is not None:
        pack_status = str(getattr(pack, "status", ""))
        if pack_status == "succeeded":
            await _send_render_pack(message, pack, variant_id=variant.variant_id)
            return
        if pack_status == "running":
            await message.answer(
                "Исходный визуал готов, render pack ещё формируется. "
                "Повторите ту же команду позже — idempotency не создаст второй render pack."
            )
            return
        if pack_status == "failed":
            error_code = str(getattr(pack, "error_code", "") or "render_failed")
            await message.answer(f"Исходный визуал готов, но render pack не создан: {error_code}")
            return

    if job.status in {"queued", "running"}:
        await message.answer(
            "Выбранный вариант ещё генерируется. Повторите ту же команду позже — "
            "idempotency не создаст вторую платную генерацию."
        )
        return
    if job.status == "succeeded":
        await message.answer("Исходный визуал готов, но render pack пока не сформирован.")
        return
    await message.answer(f"Не удалось создать вариант: {job.error_code or 'provider_failed'}")
