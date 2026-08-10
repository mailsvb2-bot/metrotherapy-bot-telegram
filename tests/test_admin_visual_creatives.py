from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from handlers import admin_visual_creatives as ui


async def immediate_to_thread(function, *args, **kwargs):
    return function(*args, **kwargs)


def message(
    text: str = "/creative_image rain",
    *,
    uid: int | None = 101,
    chat_id: int = 9001,
    message_id: int = 7001,
):
    return SimpleNamespace(
        text=text,
        message_id=message_id,
        chat=SimpleNamespace(id=chat_id),
        from_user=None if uid is None else SimpleNamespace(id=uid),
        answer=AsyncMock(),
        answer_photo=AsyncMock(),
        answer_video=AsyncMock(),
    )


def job(*, status="queued", kind="image", ready=False):
    return SimpleNamespace(
        id="gateway-job",
        provider="fake",
        kind=kind,
        status=status,
        model="m1",
        asset_ready=ready,
        error_code="" if status != "failed" else "provider_failed",
    )


def test_command_payload_uid_and_chat_id():
    msg = message("/creative_image city rain")
    assert ui._uid(msg) == 101
    assert ui._chat_id(msg) == 9001
    assert ui._command_payload(msg) == "city rain"
    assert ui._uid(message(uid=None)) is None


def test_visual_authorization_is_role_and_permission_scoped():
    with (
        patch.object(ui, "is_superadmin", return_value=False),
        patch.object(ui, "staff_roles", return_value={"support"}),
        patch.object(ui, "can_use_scoped_admin_permission", return_value=True),
    ):
        assert ui._can_use_visual_creatives(101) is False

    with (
        patch.object(ui, "is_superadmin", return_value=False),
        patch.object(ui, "staff_roles", return_value={"marketing"}),
        patch.object(ui, "can_use_scoped_admin_permission", return_value=True),
    ):
        assert ui._can_use_visual_creatives(101) is True


def test_superadmin_can_use_visual_creatives_without_db_role():
    with patch.object(ui, "is_superadmin", return_value=True):
        assert ui._can_use_visual_creatives(101) is True


def test_idempotency_key_is_unique_across_chats():
    first = ui._idempotency_key(message(chat_id=1), user_id=101, kind="image")
    second = ui._idempotency_key(message(chat_id=2), user_id=101, kind="image")
    assert first != second
    assert first == ui._idempotency_key(message(chat_id=1), user_id=101, kind="image")


@pytest.mark.asyncio
async def test_unauthorized_staff_is_denied():
    msg = message()
    with patch.object(ui, "_can_use_visual_creatives", return_value=False):
        await ui.creative_image(msg)
    assert msg.answer.await_args.args[0] == "Недоступно."


@pytest.mark.asyncio
async def test_empty_prompt_shows_usage():
    msg = message("/creative_video")
    with patch.object(ui, "_can_use_visual_creatives", return_value=True):
        await ui.creative_video(msg)
    assert "Использование" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_pending_generation_returns_status_command():
    msg = message("/creative_image night city")
    with (
        patch.object(ui, "_can_use_visual_creatives", return_value=True),
        patch.object(ui.asyncio, "to_thread", new=immediate_to_thread),
        patch.object(ui, "create_metrotherapy_visual", return_value=job()) as create_visual,
    ):
        await ui.creative_image(msg)
    assert "/creative_status gateway-job" in msg.answer.await_args.args[0]
    kwargs = create_visual.call_args.kwargs
    assert kwargs["scope_id"] == "staff:101"
    assert kwargs["idempotency_key"].startswith("metro:")


@pytest.mark.asyncio
async def test_generation_gateway_failure_is_safe():
    msg = message("/creative_image night city")
    with (
        patch.object(ui, "_can_use_visual_creatives", return_value=True),
        patch.object(ui.asyncio, "to_thread", new=immediate_to_thread),
        patch.object(
            ui,
            "create_metrotherapy_visual",
            side_effect=ui.VisualCreativeGatewayError("visual_gateway_transport_URLError"),
        ),
    ):
        await ui.creative_image(msg)
    assert "Visual Creative Gateway" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_ready_image_is_materialized_sent_and_cleaned(tmp_path):
    msg = message("/creative_image rain")
    path = tmp_path / "image.png"
    path.write_bytes(b"png")
    with (
        patch.object(ui, "_can_use_visual_creatives", return_value=True),
        patch.object(ui.asyncio, "to_thread", new=immediate_to_thread),
        patch.object(ui, "create_metrotherapy_visual", return_value=job(status="succeeded", ready=True)),
        patch.object(ui, "materialize_metrotherapy_visual", return_value=path),
    ):
        await ui.creative_image(msg)
    msg.answer_photo.assert_awaited_once()
    assert not path.exists()


@pytest.mark.asyncio
async def test_ready_video_is_materialized_sent_and_cleaned(tmp_path):
    msg = message("/creative_video rain")
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    with (
        patch.object(ui, "_can_use_visual_creatives", return_value=True),
        patch.object(ui.asyncio, "to_thread", new=immediate_to_thread),
        patch.object(ui, "create_metrotherapy_visual", return_value=job(status="succeeded", kind="video", ready=True)),
        patch.object(ui, "materialize_metrotherapy_visual", return_value=path),
    ):
        await ui.creative_video(msg)
    msg.answer_video.assert_awaited_once()
    assert not path.exists()


@pytest.mark.asyncio
async def test_materialization_gateway_failure_is_safe():
    msg = message("/creative_image rain")
    with (
        patch.object(ui, "_can_use_visual_creatives", return_value=True),
        patch.object(ui.asyncio, "to_thread", new=immediate_to_thread),
        patch.object(ui, "create_metrotherapy_visual", return_value=job(status="succeeded", ready=True)),
        patch.object(
            ui,
            "materialize_metrotherapy_visual",
            side_effect=ui.VisualCreativeGatewayError("visual_gateway_materialization_failed"),
        ),
    ):
        await ui.creative_image(msg)
    assert "безопасно получить" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_failed_job_reports_provider_error():
    msg = message("/creative_image rain")
    with (
        patch.object(ui, "_can_use_visual_creatives", return_value=True),
        patch.object(ui.asyncio, "to_thread", new=immediate_to_thread),
        patch.object(ui, "create_metrotherapy_visual", return_value=job(status="failed")),
    ):
        await ui.creative_image(msg)
    assert "provider_failed" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_status_requires_valid_job_id():
    msg = message("/creative_status ../escape")
    with (
        patch.object(ui, "_can_use_visual_creatives", return_value=True),
        patch.object(ui, "poll_metrotherapy_visual") as poll,
    ):
        await ui.creative_status(msg)
    assert "Использование" in msg.answer.await_args.args[0]
    poll.assert_not_called()


@pytest.mark.asyncio
async def test_status_poll_failure_is_safe():
    msg = message("/creative_status gateway-job")
    with (
        patch.object(ui, "_can_use_visual_creatives", return_value=True),
        patch.object(ui.asyncio, "to_thread", new=immediate_to_thread),
        patch.object(
            ui,
            "poll_metrotherapy_visual",
            side_effect=ui.VisualCreativeGatewayError("visual_gateway_transport_URLError"),
        ),
    ):
        await ui.creative_status(msg)
    assert "Не удалось" in msg.answer.await_args.args[0]
