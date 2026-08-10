from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from services import visual_creative_gateway as gateway


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        content_type: str = "application/json",
        content_length: str | None = None,
    ) -> None:
        self._stream = io.BytesIO(body)
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)) if content_length is None else content_length,
        }

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None


def test_submit_builds_gateway_request(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal:8097")
    monkeypatch.setenv("VISUAL_GATEWAY_TOKEN", "secret")
    seen = {}

    def fake_open(request, *, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.headers.get("Authorization")
        seen["payload"] = json.loads((request.data or b"{}").decode())
        assert timeout == 30
        return FakeResponse(
            b'{"id":"j1","provider":"yandexart","scope_id":"tenant-1","kind":"image","status":"queued","asset_ready":false}'
        )

    monkeypatch.setattr(gateway, "_open_request", fake_open)
    job = gateway.submit_visual(
        gateway.VisualCreativeBrief(kind="image", prompt="calm city", country_code="RU"),
        scope_id="tenant-1",
        idempotency_key="request-0001",
        wait_seconds=7,
    )
    assert job.id == "j1"
    assert seen["url"].endswith("/v1/creative/generations")
    assert seen["auth"] == "Bearer secret"
    assert seen["payload"]["country_code"] == "RU"
    assert seen["payload"]["wait_seconds"] == 7
    assert seen["payload"]["scope_id"] == "tenant-1"
    assert seen["payload"]["idempotency_key"] == "request-0001"


def test_invalid_gateway_configuration_fails_closed(monkeypatch):
    monkeypatch.delenv("VISUAL_GATEWAY_URL", raising=False)
    with pytest.raises(gateway.VisualCreativeGatewayError, match="not_configured"):
        gateway.poll_visual("job", scope_id="tenant-1")


def test_wait_polls_until_done(monkeypatch):
    sequence = [
        gateway.VisualCreativeJob(id="j", provider="x", scope_id="tenant-1", kind="video", status="running"),
        gateway.VisualCreativeJob(
            id="j",
            provider="x",
            scope_id="tenant-1",
            kind="video",
            status="succeeded",
            asset_ready=True,
        ),
    ]
    monkeypatch.setattr(gateway.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(gateway.time, "monotonic", iter([0.0, 0.1, 0.2, 0.3]).__next__)
    monkeypatch.setattr(gateway, "poll_visual", lambda _job_id, *, scope_id: sequence.pop(0))
    start = gateway.VisualCreativeJob(id="j", provider="x", scope_id="tenant-1", kind="video", status="queued")
    assert gateway.wait_visual(start, wait_seconds=1, poll_interval=0.2).status == "succeeded"


def test_download_is_bounded_and_materialized(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    body = b"video-bytes"
    monkeypatch.setattr(
        gateway,
        "_open_request",
        lambda *_args, **_kwargs: FakeResponse(body, content_type="video/mp4"),
    )
    job = gateway.VisualCreativeJob(
        id="j2",
        provider="x",
        scope_id="tenant-1",
        kind="video",
        status="succeeded",
        asset_ready=True,
    )
    path = gateway.download_visual(job, output_dir=str(tmp_path))
    assert path.read_bytes() == body
    assert path.suffix == ".mp4"
    assert list(tmp_path.glob("*.tmp")) == []


def test_download_rejects_wrong_mime(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setattr(
        gateway,
        "_open_request",
        lambda *_args, **_kwargs: FakeResponse(b"x", content_type="text/html"),
    )
    job = gateway.VisualCreativeJob(
        id="j3",
        provider="x",
        scope_id="tenant-1",
        kind="image",
        status="succeeded",
        asset_ready=True,
    )
    with pytest.raises(gateway.VisualCreativeGatewayError, match="unexpected_media_type"):
        gateway.download_visual(job, output_dir=str(tmp_path))


def test_snapshot_does_not_expose_token(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setenv("VISUAL_GATEWAY_TOKEN", "super-secret")
    rendered = repr(gateway.gateway_snapshot())
    assert "super-secret" not in rendered
    assert "token_configured" in rendered


def test_job_rejects_unsafe_identifier():
    with pytest.raises(gateway.VisualCreativeGatewayError, match="invalid_job"):
        gateway._job(
            {
                "id": "../escape",
                "scope_id": "tenant-1",
                "kind": "image",
                "status": "succeeded",
                "asset_ready": True,
            }
        )


def test_submit_timeout_covers_gateway_wait(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal:8097")
    monkeypatch.setenv("VISUAL_GATEWAY_TIMEOUT_SECONDS", "30")
    seen = {}

    def fake_open(request, *, timeout):
        seen["timeout"] = timeout
        return FakeResponse(
            b'{"id":"j4","provider":"x","scope_id":"tenant-1","kind":"video","status":"queued","asset_ready":false}'
        )

    monkeypatch.setattr(gateway, "_open_request", fake_open)
    gateway.submit_visual(
        gateway.VisualCreativeBrief(kind="video", prompt="rain"),
        scope_id="tenant-1",
        idempotency_key="request-0004",
        wait_seconds=60,
    )
    assert seen["timeout"] >= 75


def test_poll_rejects_unsafe_input_before_request(monkeypatch):
    called = False

    def fake_open(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must not be called")

    monkeypatch.setattr(gateway, "_open_request", fake_open)
    with pytest.raises(ValueError, match="valid visual job id"):
        gateway.poll_visual("../escape", scope_id="tenant-1")
    assert called is False


def test_snapshot_rejects_invalid_url_without_leaking_credentials(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://user:pass@gateway.internal:8443/private?token=x")
    snap = gateway.gateway_snapshot()
    assert snap["configured"] is False
    assert snap["base_url"] == ""
    assert "user" not in repr(snap) and "pass" not in repr(snap) and "token=x" not in repr(snap)


def test_snapshot_accepts_valid_prefixed_url_but_hides_path(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal:8443/private-prefix")
    snap = gateway.gateway_snapshot()
    assert snap["configured"] is True
    assert snap["base_url"] == "https://gateway.internal:8443"
    assert snap["secure_transport"] is True


def test_poll_includes_scope_id(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    seen = {}

    def fake_open(request, *, timeout):
        seen["url"] = request.full_url
        return FakeResponse(
            b'{"id":"j5","provider":"x","scope_id":"tenant-1","kind":"image","status":"queued","asset_ready":false}'
        )

    monkeypatch.setattr(gateway, "_open_request", fake_open)
    job = gateway.poll_visual("j5", scope_id="tenant-1")
    assert "scope_id=tenant-1" in seen["url"]
    assert job.scope_id == "tenant-1"


def test_base_url_rejects_embedded_credentials(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://user:secret@gateway.internal")
    with pytest.raises(gateway.VisualCreativeGatewayError, match="not_configured"):
        gateway._base_url()


def test_non_loopback_http_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "http://gateway.internal:8097")
    monkeypatch.delenv("VISUAL_GATEWAY_ALLOW_INSECURE_HTTP", raising=False)
    with pytest.raises(gateway.VisualCreativeGatewayError, match="insecure_http"):
        gateway._base_url()
    monkeypatch.setenv("VISUAL_GATEWAY_ALLOW_INSECURE_HTTP", "1")
    assert gateway._base_url() == "http://gateway.internal:8097"


def test_loopback_http_is_allowed_for_local_development(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "http://127.0.0.1:8097")
    monkeypatch.delenv("VISUAL_GATEWAY_ALLOW_INSECURE_HTTP", raising=False)
    assert gateway._base_url() == "http://127.0.0.1:8097"


def test_ipv6_gateway_url_is_rendered_with_brackets(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "http://[::1]:8097/prefix")
    assert gateway._base_url() == "http://[::1]:8097/prefix"
    assert gateway.gateway_snapshot()["base_url"] == "http://[::1]:8097"


def test_submit_rejects_scope_mismatch_from_gateway(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setattr(
        gateway,
        "_open_request",
        lambda *_args, **_kwargs: FakeResponse(
            b'{"id":"j6","provider":"x","scope_id":"tenant-2","kind":"image","status":"queued","asset_ready":false}'
        ),
    )
    with pytest.raises(gateway.VisualCreativeGatewayError, match="scope_mismatch"):
        gateway.submit_visual(
            gateway.VisualCreativeBrief(kind="image", prompt="rain"),
            scope_id="tenant-1",
            idempotency_key="request-0006",
        )


def test_submit_rejects_kind_mismatch_from_gateway(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setattr(
        gateway,
        "_open_request",
        lambda *_args, **_kwargs: FakeResponse(
            b'{"id":"j7","provider":"x","scope_id":"tenant-1","kind":"video","status":"queued","asset_ready":false}'
        ),
    )
    with pytest.raises(gateway.VisualCreativeGatewayError, match="kind_mismatch"):
        gateway.submit_visual(
            gateway.VisualCreativeBrief(kind="image", prompt="rain"),
            scope_id="tenant-1",
            idempotency_key="request-0007",
        )


def test_poll_rejects_scope_mismatch_from_gateway(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setattr(
        gateway,
        "_open_request",
        lambda *_args, **_kwargs: FakeResponse(
            b'{"id":"j8","provider":"x","scope_id":"tenant-2","kind":"image","status":"running","asset_ready":false}'
        ),
    )
    with pytest.raises(gateway.VisualCreativeGatewayError, match="scope_mismatch"):
        gateway.poll_visual("j8", scope_id="tenant-1")


def test_redirect_is_blocked_before_following_location(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")

    def fake_open(request, *, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            302,
            "Found",
            {"Location": "https://attacker.example/steal"},
            io.BytesIO(b"redirect"),
        )

    monkeypatch.setattr(gateway, "_open_request", fake_open)
    with pytest.raises(gateway.VisualCreativeGatewayError, match="redirect_blocked"):
        gateway.poll_visual("j9", scope_id="tenant-1")


def test_download_stream_limit_removes_partial_file(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setenv("VISUAL_GATEWAY_MAX_MEDIA_BYTES", str(1024 * 1024))
    body = b"x" * (1024 * 1024 + 1)
    monkeypatch.setattr(
        gateway,
        "_open_request",
        lambda *_args, **_kwargs: FakeResponse(body, content_type="image/jpeg", content_length=""),
    )
    job = gateway.VisualCreativeJob(
        id="j10",
        provider="x",
        scope_id="tenant-1",
        kind="image",
        status="succeeded",
        asset_ready=True,
    )
    with pytest.raises(gateway.VisualCreativeGatewayError, match="too_large"):
        gateway.download_visual(job, output_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_json_response_rejects_non_json_content_type(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setattr(
        gateway,
        "_open_request",
        lambda *_args, **_kwargs: FakeResponse(
            b'{"id":"j11","provider":"x","scope_id":"tenant-1","kind":"image","status":"queued","asset_ready":false}',
            content_type="text/plain",
        ),
    )
    with pytest.raises(gateway.VisualCreativeGatewayError, match="invalid_json_content_type"):
        gateway.poll_visual("j11", scope_id="tenant-1")


def test_gateway_metadata_rejects_markup(monkeypatch):
    monkeypatch.setenv("VISUAL_GATEWAY_URL", "https://gateway.internal")
    monkeypatch.setattr(
        gateway,
        "_open_request",
        lambda *_args, **_kwargs: FakeResponse(
            b'{"id":"j12","provider":"<b>x</b>","scope_id":"tenant-1","kind":"image","status":"queued","asset_ready":false}'
        ),
    )
    with pytest.raises(gateway.VisualCreativeGatewayError, match="invalid_job"):
        gateway.poll_visual("j12", scope_id="tenant-1")
