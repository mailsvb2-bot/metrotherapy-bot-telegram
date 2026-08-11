from __future__ import annotations

import hashlib
from io import BytesIO

import pytest

import services.visual_creative_render_gateway as render_gateway
from services import visual_creative_gateway as gateway
from services.visual_creative_gateway import VisualCreativeGatewayError, VisualCreativeJob


def _job():
    return VisualCreativeJob("job1", "fake", "staff:42", "image", "succeeded", asset_ready=True)


def _response(scope="staff:42"):
    return {
        "id": "pack1",
        "scope_id": scope,
        "source_job_id": "job1",
        "status": "succeeded",
        "error_code": "",
        "assets": [
            {
                "format_id": "story",
                "kind": "image",
                "width": 1080,
                "height": 1920,
                "mime_type": "image/jpeg",
                "sha256": hashlib.sha256(b"image").hexdigest(),
                "asset_ready": True,
                "quality": {"technical_score": 100},
            }
        ],
    }


def test_render_pack_is_exact_staff_scope_bound(monkeypatch):
    monkeypatch.setattr(gateway, "_json", lambda *a, **k: _response())
    pack = render_gateway.render_visual_pack(
        _job(),
        formats=("story",),
        composition={"headline": "x"},
        idempotency_key="metrotherapy:v1:render",
    )
    assert pack.scope_id == "staff:42"
    monkeypatch.setattr(gateway, "_json", lambda *a, **k: _response("staff:43"))
    with pytest.raises(VisualCreativeGatewayError, match="render_pack"):
        render_gateway.render_visual_pack(
            _job(),
            formats=("story",),
            composition={},
            idempotency_key="metrotherapy:v1:render",
        )


class _Response:
    def __init__(self, raw: bytes):
        self._io = BytesIO(raw)
        self.headers = {"Content-Type": "image/jpeg"}

    def read(self, n=-1):
        return self._io.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_streamed_render_download_verifies_sha(monkeypatch, tmp_path):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    pack = render_gateway._render_pack(
        _response(), expected_scope_id="staff:42", expected_source_job_id="job1"
    )
    monkeypatch.setattr(gateway, "_open_gateway_response", lambda *a, **k: _Response(b"image"))
    path = render_gateway.download_render_asset(pack, "story", output_dir=str(tmp_path))
    assert path.read_bytes() == b"image"
    monkeypatch.setattr(gateway, "_open_gateway_response", lambda *a, **k: _Response(b"bad"))
    with pytest.raises(VisualCreativeGatewayError, match="digest_mismatch"):
        render_gateway.download_render_asset(pack, "story", output_dir=str(tmp_path))


def test_succeeded_render_pack_requires_exact_format_set_kind_dimensions_and_digest(monkeypatch):
    missing_digest = _response()
    missing_digest["assets"][0]["sha256"] = ""
    monkeypatch.setattr(gateway, "_json", lambda *a, **k: missing_digest)
    with pytest.raises(VisualCreativeGatewayError, match="render_asset"):
        render_gateway.render_visual_pack(
            _job(), formats=("story",), composition={}, idempotency_key="metrotherapy:v1:render"
        )

    wrong_size = _response()
    wrong_size["assets"][0]["height"] = 1350
    monkeypatch.setattr(gateway, "_json", lambda *a, **k: wrong_size)
    with pytest.raises(VisualCreativeGatewayError, match="render_asset"):
        render_gateway.render_visual_pack(
            _job(), formats=("story",), composition={}, idempotency_key="metrotherapy:v1:render"
        )

    wrong_kind = _response()
    wrong_kind["assets"][0]["kind"] = "video"
    wrong_kind["assets"][0]["mime_type"] = "video/mp4"
    monkeypatch.setattr(gateway, "_json", lambda *a, **k: wrong_kind)
    with pytest.raises(VisualCreativeGatewayError, match="render_asset"):
        render_gateway.render_visual_pack(
            _job(), formats=("story",), composition={}, idempotency_key="metrotherapy:v1:render"
        )

    incomplete = _response()
    monkeypatch.setattr(gateway, "_json", lambda *a, **k: incomplete)
    with pytest.raises(VisualCreativeGatewayError, match="incomplete_render_pack"):
        render_gateway.render_visual_pack(
            _job(), formats=("story", "feed"), composition={}, idempotency_key="metrotherapy:v1:render"
        )


def test_render_pack_rejects_non_numeric_dimensions(monkeypatch):
    broken = _response()
    broken["assets"][0]["width"] = {"bad": "type"}
    monkeypatch.setattr(gateway, "_json", lambda *a, **k: broken)
    with pytest.raises(VisualCreativeGatewayError, match="render_asset"):
        render_gateway.render_visual_pack(
            _job(), formats=("story",), composition={}, idempotency_key="metrotherapy:v1:render"
        )


def test_render_download_rejects_metadata_response_mime_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    pack = render_gateway._render_pack(
        _response(),
        expected_scope_id="staff:42",
        expected_source_job_id="job1",
    )
    response = _Response(b"image")
    response.headers["Content-Type"] = "image/png"
    monkeypatch.setattr(gateway, "_open_gateway_response", lambda *a, **k: response)
    with pytest.raises(VisualCreativeGatewayError, match="media_type_mismatch"):
        render_gateway.download_render_asset(pack, "story", output_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == []
