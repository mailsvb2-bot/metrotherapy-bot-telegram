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
from services.metrotherapy_visual_creatives import (
    create_metrotherapy_visual,
    materialize_metrotherapy_visual,
    poll_metrotherapy_visual,
    visual_wait_seconds,
)
from services.visual_creative_capability import (
    visual_creative_country_code,
    visual_creative_enabled,
)
from services.visual_creative_gateway import VisualCreativeGatewayError

router = Router()
log = logging.getLogger(__name__)

_VISUAL_CREATIVE_ROLES = frozenset({"admin", "marketing"})
_JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


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
