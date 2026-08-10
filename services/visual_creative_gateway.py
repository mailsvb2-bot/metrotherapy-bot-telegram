from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class VisualCreativeGatewayError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VisualCreativeJob:
    id: str
    provider: str
    scope_id: str
    kind: str
    status: str
    model: str = ""
    mime_type: str = ""
    error_code: str = ""
    asset_ready: bool = False

    @property
    def done(self) -> bool:
        return self.status in {"succeeded", "failed"}


@dataclass(frozen=True, slots=True)
class VisualCreativeBrief:
    kind: str
    prompt: str
    country_code: str = ""
    preferred_provider: str = ""
    aspect_ratio: str = "1:1"
    duration_seconds: int = 5
    negative_prompt: str = ""
    reference_url: str = ""
    brand_context: str = ""
    seed: int | None = None


_JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SCOPE_ID_RE = re.compile(r"[A-Za-z0-9_.:@/-]{1,160}")
_IDEMPOTENCY_RE = re.compile(r"[A-Za-z0-9_.:@/-]{8,200}")
_PROVIDER_RE = re.compile(r"[A-Za-z0-9_.:@/+ -]{0,128}")
_MODEL_RE = re.compile(r"[A-Za-z0-9_.:@/+ -]{0,160}")
_MIME_RE = re.compile(r"[A-Za-z0-9!#$&^_.+/-]{0,128}")
_ERROR_CODE_RE = re.compile(r"[A-Za-z0-9_.:-]{0,160}")


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        del req, fp, code, msg, headers, newurl
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_RejectRedirectHandler())


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _is_loopback_host(hostname: str) -> bool:
    if hostname.rstrip(".").lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _render_host(hostname: str) -> str:
    try:
        parsed_ip = ipaddress.ip_address(hostname)
    except ValueError:
        return hostname
    if parsed_ip.version == 6:
        return f"[{hostname}]"
    return hostname


def _base_url() -> str:
    value = str(os.getenv("VISUAL_GATEWAY_URL", "") or "").strip().rstrip("/")
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise VisualCreativeGatewayError("visual_gateway_not_configured")
    parsed = urllib.parse.urlsplit(value)
    hostname = str(parsed.hostname or "")
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or "\\" in parsed.path
    ):
        raise VisualCreativeGatewayError("visual_gateway_not_configured")
    try:
        parsed_port = parsed.port
    except ValueError as exc:
        raise VisualCreativeGatewayError("visual_gateway_not_configured") from exc

    decoded_path = urllib.parse.unquote(parsed.path)
    if any(part in {".", ".."} for part in decoded_path.split("/")):
        raise VisualCreativeGatewayError("visual_gateway_not_configured")

    if parsed.scheme == "http" and not _is_loopback_host(hostname) and not _env_bool(
        "VISUAL_GATEWAY_ALLOW_INSECURE_HTTP"
    ):
        raise VisualCreativeGatewayError("visual_gateway_insecure_http")

    port = f":{parsed_port}" if parsed_port else ""
    prefix = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{_render_host(hostname)}{port}{prefix}"


def _headers(*, json_body: bool = False) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    token = str(os.getenv("VISUAL_GATEWAY_TOKEN", "") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _open_request(request: urllib.request.Request, *, timeout: int) -> Any:
    return _NO_REDIRECT_OPENER.open(request, timeout=timeout)  # nosec B310 - validated operator gateway URL, redirects disabled


def _open_gateway_response(request: urllib.request.Request, *, timeout: int) -> Any:
    try:
        return _open_request(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            exc.read(65536)
        except OSError:
            pass
        code = int(exc.code)
        if 300 <= code < 400:
            raise VisualCreativeGatewayError("visual_gateway_redirect_blocked") from None
        raise VisualCreativeGatewayError(f"visual_gateway_http_{code}") from None
    except urllib.error.URLError as exc:
        raise VisualCreativeGatewayError("visual_gateway_transport_URLError") from exc
    except TimeoutError as exc:
        raise VisualCreativeGatewayError("visual_gateway_transport_TimeoutError") from exc
    except OSError as exc:
        raise VisualCreativeGatewayError("visual_gateway_transport_OSError") from exc
    except ValueError as exc:
        raise VisualCreativeGatewayError("visual_gateway_transport_ValueError") from exc


def _check_content_length(response: Any, limit: int) -> None:
    content_length = str(response.headers.get("Content-Length") or "").strip()
    if not content_length:
        return
    try:
        length = int(content_length)
    except ValueError:
        return
    if length < 0 or length > limit:
        raise VisualCreativeGatewayError("visual_gateway_response_too_large")


def _read_limited(response: Any, limit: int) -> bytes:
    _check_content_length(response, limit)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise VisualCreativeGatewayError("visual_gateway_response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def _write_limited(response: Any, handle: Any, limit: int) -> None:
    _check_content_length(response, limit)
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise VisualCreativeGatewayError("visual_gateway_response_too_large")
        handle.write(chunk)


def _request_object(method: str, path: str, *, payload: dict[str, Any] | None = None) -> urllib.request.Request:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return urllib.request.Request(
        _base_url() + path,
        data=body,
        method=str(method).upper(),
        headers=_headers(json_body=payload is not None),
    )


def _request_timeout(timeout_seconds: int | None = None) -> int:
    configured_timeout = _env_int("VISUAL_GATEWAY_TIMEOUT_SECONDS", 30, minimum=3, maximum=300)
    if timeout_seconds is None:
        return configured_timeout
    return max(configured_timeout, min(int(timeout_seconds), 300))


def _request(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    max_bytes: int,
    timeout_seconds: int | None = None,
) -> tuple[dict[str, str], bytes]:
    request = _request_object(method, path, payload=payload)
    response = _open_gateway_response(request, timeout=_request_timeout(timeout_seconds))
    with response:
        raw = _read_limited(response, max_bytes)
        headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
    return headers, raw


def _json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    headers, raw = _request(
        method,
        path,
        payload=payload,
        max_bytes=_env_int("VISUAL_GATEWAY_MAX_JSON_BYTES", 1024 * 1024, minimum=65536, maximum=8 * 1024 * 1024),
        timeout_seconds=timeout_seconds,
    )
    content_type = str(headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type and content_type != "application/json" and not content_type.endswith("+json"):
        raise VisualCreativeGatewayError("visual_gateway_invalid_json_content_type")
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VisualCreativeGatewayError("visual_gateway_invalid_json") from exc
    if not isinstance(value, dict):
        raise VisualCreativeGatewayError("visual_gateway_invalid_response")
    return value


def _job(
    value: dict[str, Any],
    *,
    expected_scope_id: str | None = None,
    expected_kind: str | None = None,
) -> VisualCreativeJob:
    job_id = str(value.get("id") or "").strip()
    status = str(value.get("status") or "failed").strip().lower()
    kind = str(value.get("kind") or "").strip().lower()
    scope_id = str(value.get("scope_id") or "").strip()
    provider = str(value.get("provider") or "").strip()
    model = str(value.get("model") or "").strip()
    mime_type = str(value.get("mime_type") or "").strip().lower()
    error_code = str(value.get("error_code") or "").strip()
    if (
        not _JOB_ID_RE.fullmatch(job_id)
        or not _SCOPE_ID_RE.fullmatch(scope_id)
        or kind not in {"image", "video"}
        or status not in {"queued", "running", "succeeded", "failed"}
        or not _PROVIDER_RE.fullmatch(provider)
        or not _MODEL_RE.fullmatch(model)
        or not _MIME_RE.fullmatch(mime_type)
        or not _ERROR_CODE_RE.fullmatch(error_code)
    ):
        raise VisualCreativeGatewayError("visual_gateway_invalid_job")
    if expected_scope_id is not None and scope_id != expected_scope_id:
        raise VisualCreativeGatewayError("visual_gateway_scope_mismatch")
    if expected_kind is not None and kind != expected_kind:
        raise VisualCreativeGatewayError("visual_gateway_kind_mismatch")
    return VisualCreativeJob(
        id=job_id,
        provider=provider,
        scope_id=scope_id,
        kind=kind,
        status=status,
        model=model,
        mime_type=mime_type,
        error_code=error_code,
        asset_ready=bool(value.get("asset_ready")),
    )


def submit_visual(
    brief: VisualCreativeBrief,
    *,
    scope_id: str,
    idempotency_key: str,
    wait_seconds: int = 0,
) -> VisualCreativeJob:
    kind = str(brief.kind or "").strip().lower()
    prompt = str(brief.prompt or "").strip()
    if kind not in {"image", "video"} or not prompt:
        raise ValueError("valid visual kind and prompt are required")
    scope = str(scope_id or "").strip()
    idem = str(idempotency_key or "").strip()
    if not _SCOPE_ID_RE.fullmatch(scope) or not _IDEMPOTENCY_RE.fullmatch(idem):
        raise ValueError("valid visual scope and idempotency key are required")
    bounded_wait = max(0, min(int(wait_seconds or 0), 60))
    payload = {
        "kind": kind,
        "prompt": prompt,
        "country_code": str(brief.country_code or ""),
        "preferred_provider": str(brief.preferred_provider or ""),
        "aspect_ratio": str(brief.aspect_ratio or "1:1"),
        "duration_seconds": max(2, min(int(brief.duration_seconds or 5), 15)),
        "negative_prompt": str(brief.negative_prompt or ""),
        "reference_url": str(brief.reference_url or ""),
        "brand_context": str(brief.brand_context or ""),
        "wait_seconds": bounded_wait,
        "seed": brief.seed,
        "scope_id": scope,
        "idempotency_key": idem,
    }
    return _job(
        _json("POST", "/v1/creative/generations", payload=payload, timeout_seconds=bounded_wait + 15),
        expected_scope_id=scope,
        expected_kind=kind,
    )


def poll_visual(job_id: str, *, scope_id: str) -> VisualCreativeJob:
    raw = str(job_id or "").strip()
    scope = str(scope_id or "").strip()
    if not _JOB_ID_RE.fullmatch(raw):
        raise ValueError("valid visual job id is required")
    if not _SCOPE_ID_RE.fullmatch(scope):
        raise ValueError("valid visual scope is required")
    token = urllib.parse.quote(raw, safe="")
    query = urllib.parse.urlencode({"scope_id": scope})
    return _job(
        _json("GET", f"/v1/creative/generations/{token}?{query}"),
        expected_scope_id=scope,
    )


def wait_visual(job: VisualCreativeJob, *, wait_seconds: int = 20, poll_interval: float = 2.0) -> VisualCreativeJob:
    if job.done or wait_seconds <= 0:
        return job
    deadline = time.monotonic() + max(0, min(int(wait_seconds), 60))
    current = job
    while time.monotonic() < deadline and not current.done:
        time.sleep(max(0.2, min(float(poll_interval), 5.0)))
        current = poll_visual(current.id, scope_id=current.scope_id)
    return current


def _remove_partial_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _media_suffix(mime: str, *, kind: str) -> str:
    suffix = mimetypes.guess_extension(mime) if mime else None
    if suffix == ".jpe":
        suffix = ".jpg"
    return suffix or (".mp4" if kind == "video" else ".jpg")


def download_visual(job: VisualCreativeJob, *, output_dir: str | None = None) -> Path:
    if job.status != "succeeded" or not job.asset_ready:
        raise VisualCreativeGatewayError("visual_content_not_ready")
    if not _JOB_ID_RE.fullmatch(str(job.id or "")):
        raise VisualCreativeGatewayError("visual_gateway_invalid_job")
    if not _SCOPE_ID_RE.fullmatch(str(job.scope_id or "")):
        raise VisualCreativeGatewayError("visual_gateway_invalid_scope")
    if job.kind not in {"image", "video"}:
        raise VisualCreativeGatewayError("visual_gateway_invalid_job")

    token = urllib.parse.quote(job.id, safe="")
    query = urllib.parse.urlencode({"scope_id": job.scope_id})
    max_media = _env_int(
        "VISUAL_GATEWAY_MAX_MEDIA_BYTES",
        256 * 1024 * 1024,
        minimum=1024 * 1024,
        maximum=1024 * 1024 * 1024,
    )
    request = _request_object("GET", f"/v1/creative/generations/{token}/content?{query}")
    response = _open_gateway_response(request, timeout=_request_timeout())
    temp_path: Path | None = None
    try:
        with response:
            response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            mime = str(response_headers.get("content-type") or job.mime_type or "").split(";", 1)[0].strip().lower()
            expected = "video/" if job.kind == "video" else "image/"
            if mime and mime != "application/octet-stream" and not mime.startswith(expected):
                raise VisualCreativeGatewayError("visual_gateway_unexpected_media_type")
            suffix = _media_suffix(mime, kind=job.kind)
            root = Path(output_dir or os.getenv("VISUAL_CREATIVE_OUTPUT_DIR", "data/visual_creatives")).expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
            target = root / f"{job.kind}-{job.id}{suffix}"
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=root,
                prefix=f".{job.kind}-{job.id}-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                _write_limited(response, handle, max_media)
            os.replace(temp_path, target)
            temp_path = None
            return target
    except VisualCreativeGatewayError:
        _remove_partial_file(temp_path)
        raise
    except OSError as exc:
        _remove_partial_file(temp_path)
        raise VisualCreativeGatewayError("visual_gateway_materialization_failed") from exc


def gateway_snapshot() -> dict[str, Any]:
    safe_base = ""
    secure_transport = False
    try:
        configured = _base_url()
    except VisualCreativeGatewayError:
        configured = ""
    if configured:
        parsed = urllib.parse.urlsplit(configured)
        hostname = str(parsed.hostname or "")
        port = f":{parsed.port}" if parsed.port else ""
        safe_base = f"{parsed.scheme}://{_render_host(hostname)}{port}"
        secure_transport = parsed.scheme == "https"
    return {
        "configured": bool(safe_base),
        "base_url": safe_base,
        "secure_transport": secure_transport,
        "token_configured": bool(str(os.getenv("VISUAL_GATEWAY_TOKEN", "") or "").strip()),
    }


__all__ = [
    "VisualCreativeBrief",
    "VisualCreativeGatewayError",
    "VisualCreativeJob",
    "download_visual",
    "gateway_snapshot",
    "poll_visual",
    "submit_visual",
    "wait_visual",
]
