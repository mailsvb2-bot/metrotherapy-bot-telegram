from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from handlers import admin_visual_creatives as ui
from services.visual_creative_render_gateway import VisualRenderAsset, VisualRenderPack


async def immediate_to_thread(function, *args, **kwargs):
    return function(*args, **kwargs)


def message(text: str, *, uid: int = 101):
    return SimpleNamespace(
        text=text,
        message_id=7001,
        chat=SimpleNamespace(id=9001),
        from_user=SimpleNamespace(id=uid),
        answer=AsyncMock(),
        answer_photo=AsyncMock(),
        answer_video=AsyncMock(),
    )


def test_studio_authorization_requires_studio_flag():
    with (
        patch.object(ui, "visual_creative_studio_enabled", return_value=False),
        patch.object(ui, "_can_use_visual_creatives", return_value=True),
    ):
        assert ui._can_use_creative_studio(101) is False
    with (
        patch.object(ui, "visual_creative_studio_enabled", return_value=True),
        patch.object(ui, "_can_use_visual_creatives", return_value=True),
    ):
        assert ui._can_use_creative_studio(101) is True


@pytest.mark.asyncio
async def test_concepts_are_local_and_country_bound():
    msg = message("/creative_concepts quiet city")
    variants = tuple(
        SimpleNamespace(angle_id=x) for x in ("night_city", "nature_breath", "warm_human")
    )
    with (
        patch.object(ui, "_can_use_creative_studio", return_value=True),
        patch.object(ui, "visual_creative_country_code", return_value="NL"),
        patch.object(ui, "build_metrotherapy_studio_variants", return_value=variants) as build,
    ):
        await ui.creative_concepts(msg)
    assert build.call_args.kwargs["country_code"] == "NL"
    assert "3 направления" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_pack_pending_reuses_stable_command_identity():
    msg = message("/creative_pack image 2 quiet city")
    variants = tuple(SimpleNamespace(variant_id=f"v{i}") for i in range(3))
    job = SimpleNamespace(status="running", error_code="")
    with (
        patch.object(ui, "_can_use_creative_studio", return_value=True),
        patch.object(ui, "visual_creative_country_code", return_value="RU"),
        patch.object(ui, "build_metrotherapy_studio_variants", return_value=variants) as build,
        patch.object(ui.asyncio, "to_thread", new=immediate_to_thread),
        patch.object(ui, "submit_metrotherapy_studio_variant", return_value=(job, None)) as submit,
    ):
        await ui.creative_pack(msg)
    assert build.call_args.kwargs["country_code"] == "RU"
    assert submit.call_args.args[0].variant_id == "v1"
    assert submit.call_args.kwargs["staff_user_id"] == 101
    assert "вторую платную генерацию" in msg.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_ready_render_preview_is_sent_and_cleaned(tmp_path: Path):
    msg = message("/creative_pack image 1 quiet city")
    variants = (SimpleNamespace(variant_id="variant-1"),) * 3
    asset = VisualRenderAsset("story", "image", 1080, 1920, "image/jpeg", "a" * 64, True, {})
    pack = VisualRenderPack("pack1", "staff:101", "job1", "succeeded", "", (asset,))
    job = SimpleNamespace(status="succeeded", error_code="")
    path = tmp_path / "preview.jpg"
    path.write_bytes(b"image")
    with (
        patch.object(ui, "_can_use_creative_studio", return_value=True),
        patch.object(ui, "build_metrotherapy_studio_variants", return_value=variants),
        patch.object(ui.asyncio, "to_thread", new=immediate_to_thread),
        patch.object(ui, "submit_metrotherapy_studio_variant", return_value=(job, pack)),
        patch.object(ui, "download_render_asset", return_value=path),
    ):
        await ui.creative_pack(msg)
    msg.answer_photo.assert_awaited_once()
    assert not path.exists()


@pytest.mark.asyncio
async def test_failed_render_pack_reports_render_failure():
    msg = message("/creative_pack image 1 quiet city")
    variants = (SimpleNamespace(variant_id="variant-1"),) * 3
    pack = SimpleNamespace(status="failed", error_code="compositor_failed")
    job = SimpleNamespace(status="succeeded", error_code="")
    with (
        patch.object(ui, "_can_use_creative_studio", return_value=True),
        patch.object(ui, "build_metrotherapy_studio_variants", return_value=variants),
        patch.object(ui.asyncio, "to_thread", new=immediate_to_thread),
        patch.object(ui, "submit_metrotherapy_studio_variant", return_value=(job, pack)),
    ):
        await ui.creative_pack(msg)
    assert "compositor_failed" in msg.answer.await_args.args[0]
