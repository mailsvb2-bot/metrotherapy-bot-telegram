from __future__ import annotations

import hashlib
import os
import re
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services import visual_creative_gateway as gateway


@dataclass(frozen=True, slots=True)
class VisualRenderAsset:
    format_id: str
    kind: str
    width: int
    height: int
    mime_type: str
    sha256: str
    asset_ready: bool
    quality: dict[str, Any]


@dataclass(frozen=True, slots=True)
class VisualRenderPack:
    id: str
    scope_id: str
    source_job_id: str
    status: str
    error_code: str
    assets: tuple[VisualRenderAsset, ...]


_RENDER_PACK_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_RENDER_SHA_RE = re.compile(r"[0-9a-f]{64}")
_RENDER_FORMATS = frozenset({"square", "feed", "story", "landscape"})
_RENDER_DIMENSIONS = {
    "square": (1080, 1080),
    "feed": (1080, 1350),
    "story": (1080, 1920),
    "landscape": (1200, 628),
}


def _render_pack(
    value: dict[str, Any],
    *,
    expected_scope_id: str,
    expected_source_job_id: str,
    expected_formats: tuple[str, ...] = (),
    expected_kind: str = "",
) -> VisualRenderPack:
    pack_id = str(value.get("id") or "").strip()
    scope_id = str(value.get("scope_id") or "").strip()
    source_job_id = str(value.get("source_job_id") or "").strip()
    status = str(value.get("status") or "failed").strip().lower()
    error_code = str(value.get("error_code") or "").strip()
    raw_assets = value.get("assets")
    if (
        _RENDER_PACK_ID_RE.fullmatch(pack_id) is None
        or gateway._SCOPE_ID_RE.fullmatch(scope_id) is None
        or scope_id != expected_scope_id
        or gateway._JOB_ID_RE.fullmatch(source_job_id) is None
        or source_job_id != expected_source_job_id
        or status not in {"running", "succeeded", "failed"}
        or gateway._ERROR_CODE_RE.fullmatch(error_code) is None
        or not isinstance(raw_assets, list)
        or len(raw_assets) > len(_RENDER_FORMATS)
    ):
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_render_pack")

    selected = tuple(dict.fromkeys(str(item or "").strip().lower() for item in expected_formats))
    if any(item not in _RENDER_FORMATS for item in selected):
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_render_pack")
    expected_kind = str(expected_kind or "").strip().lower()
    if expected_kind and expected_kind not in {"image", "video"}:
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_render_pack")

    assets: list[VisualRenderAsset] = []
    seen: set[str] = set()
    for item in raw_assets:
        if not isinstance(item, dict):
            raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_render_asset")
        format_id = str(item.get("format_id") or "").strip().lower()
        kind = str(item.get("kind") or "").strip().lower()
        mime_type = str(item.get("mime_type") or "").strip().lower()
        sha256 = str(item.get("sha256") or "").strip().lower()
        ready = item.get("asset_ready")
        try:
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
        except (TypeError, ValueError) as exc:
            raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_render_asset") from exc

        if (
            format_id not in _RENDER_FORMATS
            or format_id in seen
            or kind not in {"image", "video"}
            or not isinstance(ready, bool)
            or (width, height) != _RENDER_DIMENSIONS[format_id]
            or (expected_kind and kind != expected_kind)
            or not mime_type
            or gateway._MIME_RE.fullmatch(mime_type) is None
            or not mime_type.startswith("video/" if kind == "video" else "image/")
            or (status == "succeeded" and (_RENDER_SHA_RE.fullmatch(sha256) is None or ready is not True))
            or (sha256 and _RENDER_SHA_RE.fullmatch(sha256) is None)
        ):
            raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_render_asset")

        seen.add(format_id)
        assets.append(
            VisualRenderAsset(
                format_id=format_id,
                kind=kind,
                width=width,
                height=height,
                mime_type=mime_type,
                sha256=sha256,
                asset_ready=ready,
                quality=dict(item.get("quality") or {}) if isinstance(item.get("quality"), dict) else {},
            )
        )

    if status == "succeeded":
        if not assets or (selected and set(seen) != set(selected)):
            raise gateway.VisualCreativeGatewayError("visual_gateway_incomplete_render_pack")
    elif selected and any(item not in set(selected) for item in seen):
        raise gateway.VisualCreativeGatewayError("visual_gateway_unexpected_render_format")

    return VisualRenderPack(
        id=pack_id,
        scope_id=scope_id,
        source_job_id=source_job_id,
        status=status,
        error_code=error_code,
        assets=tuple(assets),
    )


def render_visual_pack(
    job: gateway.VisualCreativeJob,
    *,
    formats: tuple[str, ...],
    composition: dict[str, Any],
    idempotency_key: str,
) -> VisualRenderPack:
    if job.status != "succeeded" or not job.asset_ready:
        raise gateway.VisualCreativeGatewayError("visual_source_not_ready")
    if gateway._JOB_ID_RE.fullmatch(str(job.id or "")) is None:
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_job")
    if gateway._SCOPE_ID_RE.fullmatch(str(job.scope_id or "")) is None:
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_scope")
    if job.kind not in {"image", "video"}:
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_job")

    selected: list[str] = []
    for raw in formats:
        token = str(raw or "").strip().lower()
        if token not in _RENDER_FORMATS:
            raise ValueError("invalid_visual_render_format")
        if token not in selected:
            selected.append(token)
    if not selected or len(selected) > len(_RENDER_FORMATS):
        raise ValueError("visual_render_formats_required")

    idem = str(idempotency_key or "").strip()
    if gateway._IDEMPOTENCY_RE.fullmatch(idem) is None:
        raise ValueError("visual_render_idempotency_key_invalid")
    if not isinstance(composition, dict):
        raise ValueError("visual_render_composition_required")

    return _render_pack(
        gateway._json(
            "POST",
            "/v1/creative/render-packs",
            payload={
                "source_job_id": job.id,
                "scope_id": job.scope_id,
                "idempotency_key": idem,
                "formats": selected,
                "composition": dict(composition),
            },
            timeout_seconds=300,
        ),
        expected_scope_id=job.scope_id,
        expected_source_job_id=job.id,
        expected_formats=tuple(selected),
        expected_kind=job.kind,
    )


def download_render_asset(
    pack: VisualRenderPack,
    format_id: str,
    *,
    output_dir: str | None = None,
) -> Path:
    token = str(format_id or "").strip().lower()
    asset = next((item for item in pack.assets if item.format_id == token), None)
    if pack.status != "succeeded" or asset is None or not asset.asset_ready:
        raise gateway.VisualCreativeGatewayError("visual_render_content_not_ready")
    if _RENDER_PACK_ID_RE.fullmatch(str(pack.id or "")) is None:
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_render_asset")
    if gateway._SCOPE_ID_RE.fullmatch(str(pack.scope_id or "")) is None:
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_scope")
    if gateway._JOB_ID_RE.fullmatch(str(pack.source_job_id or "")) is None:
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_render_asset")
    if token not in _RENDER_FORMATS or asset.kind not in {"image", "video"}:
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_render_asset")
    if _RENDER_SHA_RE.fullmatch(str(asset.sha256 or "").lower()) is None:
        raise gateway.VisualCreativeGatewayError("visual_gateway_invalid_render_asset")

    query = urllib.parse.urlencode({"scope_id": pack.scope_id})
    request = gateway._request_object(
        "GET",
        "/v1/creative/render-packs/"
        + urllib.parse.quote(pack.id, safe="")
        + "/content/"
        + urllib.parse.quote(token, safe="")
        + f"?{query}",
    )
    response = gateway._open_gateway_response(request, timeout=gateway._request_timeout(None))
    max_media = gateway._env_int(
        "VISUAL_GATEWAY_MAX_MEDIA_BYTES",
        256 * 1024 * 1024,
        minimum=1024 * 1024,
        maximum=1024 * 1024 * 1024,
    )
    temp_path: Path | None = None
    root = Path(
        output_dir or os.getenv("VISUAL_CREATIVE_OUTPUT_DIR", "data/visual_creatives")
    ).expanduser().resolve()
    try:
        root.mkdir(parents=True, exist_ok=True)
        with response:
            response_type = (
                str(response.headers.get("Content-Type") or "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if response_type and response_type != "application/octet-stream":
                if response_type != asset.mime_type:
                    raise gateway.VisualCreativeGatewayError("visual_gateway_render_media_type_mismatch")
                content_type = response_type
            else:
                content_type = asset.mime_type
            expected = "video/" if asset.kind == "video" else "image/"
            if not content_type.startswith(expected):
                raise gateway.VisualCreativeGatewayError("visual_gateway_unexpected_render_media_type")
            suffix = gateway._media_suffix(content_type, kind=asset.kind)
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=root,
                prefix=f".render-{pack.id}-{token}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                gateway._write_limited(response, handle, max_media)

        digest = hashlib.sha256()
        with temp_path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != asset.sha256:
            gateway._remove_partial_file(temp_path)
            raise gateway.VisualCreativeGatewayError("visual_gateway_render_digest_mismatch")

        target = root / f"render-{pack.id}-{token}{suffix}"
        os.replace(temp_path, target)
        temp_path = None
        return target
    except gateway.VisualCreativeGatewayError:
        gateway._remove_partial_file(temp_path)
        raise
    except OSError as exc:
        gateway._remove_partial_file(temp_path)
        raise gateway.VisualCreativeGatewayError("visual_gateway_render_materialization_failed") from exc


__all__ = [
    "VisualRenderAsset",
    "VisualRenderPack",
    "download_render_asset",
    "render_visual_pack",
]
