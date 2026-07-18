from __future__ import annotations

"""Safely plan, register and verify the official MAX webhook subscription.

Dry-run is the default and performs no provider network calls. ``--apply`` is
required before the helper can contact MAX or mutate subscriptions. Reports are
JSON-only and never contain the bot token, webhook secret, request headers or raw
provider response bodies.
"""

import argparse
import json
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request  # nosec B310 - fixed official HTTPS origin is validated before use
from dataclasses import asdict, dataclass
from typing import Any

MAX_PLATFORM_API_BASE_URL = "https://platform-api2.max.ru"
MAX_WEBHOOK_UPDATE_TYPES = ("message_created", "message_callback", "bot_started")
_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{5,256}")
_ERROR_CODE_RE = re.compile(r"[^A-Za-z0-9_.:-]+")


class MaxWebhookRegistrationError(RuntimeError):
    """Expected configuration or provider-boundary failure."""

    def __init__(self, code: str, *, network_called: bool = False) -> None:
        normalized = str(code or "registration_failed")
        super().__init__(normalized)
        self.code = normalized
        self.network_called = bool(network_called)


@dataclass(frozen=True)
class MaxWebhookConfig:
    api_base_url: str
    token: str
    public_base_url: str
    secret: str
    ca_bundle: str = ""

    @property
    def webhook_url(self) -> str:
        return self.public_base_url.rstrip("/") + "/webhooks/max"


@dataclass(frozen=True)
class MaxApiResponse:
    status: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class MaxWebhookReport:
    ok: bool
    mode: str
    applied: bool
    network_called: bool
    api_base_url: str
    webhook_url: str
    update_types: tuple[str, ...]
    ca_bundle_configured: bool
    was_already_present: bool | None = None
    created: bool | None = None
    active_after: bool | None = None
    bot: dict[str, Any] | None = None
    provider_statuses: dict[str, int] | None = None
    error_code: str = ""


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _validated_public_base(raw: str) -> str:
    value = str(raw or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        raise MaxWebhookRegistrationError("public_base_must_be_https")
    if parsed.username or parsed.password:
        raise MaxWebhookRegistrationError("public_base_must_not_contain_credentials")
    if parsed.query or parsed.fragment:
        raise MaxWebhookRegistrationError("public_base_must_not_contain_query_or_fragment")
    if parsed.path not in {"", "/"}:
        raise MaxWebhookRegistrationError("public_base_must_not_contain_path")
    try:
        port = parsed.port
    except ValueError as exc:
        raise MaxWebhookRegistrationError("public_base_port_invalid") from exc
    if port is not None and not (1 <= int(port) <= 65535):
        raise MaxWebhookRegistrationError("public_base_port_invalid")
    return value


def _validated_config() -> MaxWebhookConfig:
    token = _env("MAX_BOT_TOKEN")
    public_base = _env("MESSENGER_PUBLIC_BASE_URL")
    secret = _env("MAX_WEBHOOK_SECRET", _env("MAX_SECRET_KEY"))
    missing: list[str] = []
    if not token:
        missing.append("MAX_BOT_TOKEN")
    if not public_base:
        missing.append("MESSENGER_PUBLIC_BASE_URL")
    if not secret:
        missing.append("MAX_WEBHOOK_SECRET")
    if missing:
        raise MaxWebhookRegistrationError("missing_config:" + ",".join(missing))
    if not _SECRET_RE.fullmatch(secret):
        raise MaxWebhookRegistrationError("webhook_secret_format_invalid")

    api_base = _env("MAX_API_BASE_URL", MAX_PLATFORM_API_BASE_URL).rstrip("/")
    if api_base != MAX_PLATFORM_API_BASE_URL:
        raise MaxWebhookRegistrationError("official_api2_required")

    ca_bundle = _env("MAX_CA_BUNDLE")
    if ca_bundle and not os.path.isfile(ca_bundle):
        raise MaxWebhookRegistrationError("ca_bundle_missing")

    return MaxWebhookConfig(
        api_base_url=api_base,
        token=token,
        public_base_url=_validated_public_base(public_base),
        secret=secret,
        ca_bundle=ca_bundle,
    )


def _compat_config_message(code: str) -> str:
    if code.startswith("missing_config:"):
        return "Missing required environment variables: " + code.split(":", 1)[1].replace(",", ", ")
    if code == "webhook_secret_format_invalid":
        return "MAX_WEBHOOK_SECRET must match [A-Za-z0-9_-]{5,256}"
    if code == "official_api2_required":
        return "MAX_API_BASE_URL must be exactly https://platform-api2.max.ru"
    if code == "ca_bundle_missing":
        return "MAX_CA_BUNDLE file does not exist"
    if code.startswith("public_base_"):
        return "MESSENGER_PUBLIC_BASE_URL must be a bare public HTTPS origin"
    return code


def _load_config() -> MaxWebhookConfig:
    """Compatibility wrapper retained for focused configuration tests."""

    try:
        return _validated_config()
    except MaxWebhookRegistrationError as exc:
        raise SystemExit(_compat_config_message(exc.code)) from None


def _ssl_context(ca_bundle: str = "") -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_bundle or None)


def _safe_transport_error(exc: BaseException) -> str:
    reason: BaseException | object = exc
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
    if isinstance(reason, ssl.SSLCertVerificationError):
        return f"tls_cert_verify_{int(reason.verify_code)}"
    if isinstance(reason, ssl.SSLError):
        return "tls_error"
    if isinstance(reason, socket.gaierror):
        return "dns_error"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "network_timeout"
    if isinstance(reason, ConnectionRefusedError):
        return "connection_refused"
    return "network_error"


def _safe_provider_error(payload: dict[str, Any], *, fallback: str) -> str:
    """Use structured provider codes only; never echo free-form messages."""

    raw: Any = payload.get("code")
    nested = payload.get("error")
    if raw is None and isinstance(nested, dict):
        raw = nested.get("code")
    if raw is None:
        return fallback
    compact = _ERROR_CODE_RE.sub("_", str(raw))[:80].strip("_")
    return compact or fallback


def _json_request(
    url: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    ca_bundle: str = "",
    timeout_sec: int = 30,
) -> MaxApiResponse:
    if not url.startswith(MAX_PLATFORM_API_BASE_URL + "/"):
        raise MaxWebhookRegistrationError("provider_origin_rejected")

    body = None
    headers = {"Authorization": token}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        context = _ssl_context(ca_bundle)
        with urllib.request.urlopen(  # nosec B310 - URL origin checked immediately above
            request,
            timeout=max(1, min(int(timeout_sec), 60)),
            context=context,
        ) as response:
            raw = response.read().decode("utf-8", "replace")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = int(exc.code)
    except urllib.error.URLError as exc:
        raise MaxWebhookRegistrationError(
            _safe_transport_error(exc),
            network_called=True,
        ) from None
    except ssl.SSLError as exc:
        raise MaxWebhookRegistrationError(
            _safe_transport_error(exc),
            network_called=True,
        ) from None
    except OSError as exc:
        raise MaxWebhookRegistrationError(
            _safe_transport_error(exc),
            network_called=True,
        ) from None

    try:
        loaded = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        loaded = {"error": "invalid_json"}
    if isinstance(loaded, dict):
        payload_dict = loaded
    elif isinstance(loaded, list):
        payload_dict = {"subscriptions": loaded}
    else:
        payload_dict = {"error": "invalid_response"}
    return MaxApiResponse(status=status, payload=payload_dict)


def _redact_text(value: Any, *, cfg: MaxWebhookConfig) -> str:
    text = str(value or "")
    for secret in (cfg.token, cfg.secret):
        if secret:
            text = text.replace(secret, "redacted")
    return " ".join(text.split())[:160]


def _safe_bot_summary(me: dict[str, Any], *, cfg: MaxWebhookConfig) -> dict[str, Any]:
    return {
        "user_id": me.get("user_id"),
        "username": _redact_text(me.get("username"), cfg=cfg),
        "name": _redact_text(me.get("name"), cfg=cfg),
        "is_bot": me.get("is_bot"),
    }


def _subscription_urls(data: dict[str, Any]) -> list[str]:
    subscriptions = data.get("subscriptions") or data.get("items") or data.get("data") or []
    urls: list[str] = []
    if not isinstance(subscriptions, list):
        return urls
    for item in subscriptions:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]).rstrip("/"))
    return urls


def _require_success(response: MaxApiResponse, *, stage: str) -> None:
    invalid_payload = response.payload.get("error") in {"invalid_json", "invalid_response"}
    if 200 <= int(response.status) < 300 and not invalid_payload:
        return
    code = _safe_provider_error(response.payload, fallback="provider_response_invalid")
    raise MaxWebhookRegistrationError(
        f"{stage}_http_{response.status}:{code}",
        network_called=True,
    )


def _dry_run_report(cfg: MaxWebhookConfig) -> MaxWebhookReport:
    return MaxWebhookReport(
        ok=True,
        mode="dry_run",
        applied=False,
        network_called=False,
        api_base_url=cfg.api_base_url,
        webhook_url=cfg.webhook_url,
        update_types=MAX_WEBHOOK_UPDATE_TYPES,
        ca_bundle_configured=bool(cfg.ca_bundle),
    )


def _apply_registration(cfg: MaxWebhookConfig, *, timeout_sec: int) -> MaxWebhookReport:
    me = _json_request(
        cfg.api_base_url + "/me",
        token=cfg.token,
        method="GET",
        ca_bundle=cfg.ca_bundle,
        timeout_sec=timeout_sec,
    )
    _require_success(me, stage="me")

    before = _json_request(
        cfg.api_base_url + "/subscriptions",
        token=cfg.token,
        method="GET",
        ca_bundle=cfg.ca_bundle,
        timeout_sec=timeout_sec,
    )
    _require_success(before, stage="subscriptions_before")
    existing_urls = _subscription_urls(before.payload)
    already_present = cfg.webhook_url.rstrip("/") in existing_urls

    created = False
    create_status = 0
    if not already_present:
        create = _json_request(
            cfg.api_base_url + "/subscriptions",
            token=cfg.token,
            method="POST",
            payload={
                "url": cfg.webhook_url,
                "update_types": list(MAX_WEBHOOK_UPDATE_TYPES),
                "secret": cfg.secret,
            },
            ca_bundle=cfg.ca_bundle,
            timeout_sec=timeout_sec,
        )
        create_status = int(create.status)
        _require_success(create, stage="subscription_create")
        if create.payload.get("success") is False:
            raise MaxWebhookRegistrationError(
                "subscription_create_rejected",
                network_called=True,
            )
        created = True

    after = _json_request(
        cfg.api_base_url + "/subscriptions",
        token=cfg.token,
        method="GET",
        ca_bundle=cfg.ca_bundle,
        timeout_sec=timeout_sec,
    )
    _require_success(after, stage="subscriptions_after")
    active_after = cfg.webhook_url.rstrip("/") in _subscription_urls(after.payload)
    if not active_after:
        raise MaxWebhookRegistrationError(
            "subscription_not_visible_after_apply",
            network_called=True,
        )

    return MaxWebhookReport(
        ok=True,
        mode="apply",
        applied=True,
        network_called=True,
        api_base_url=cfg.api_base_url,
        webhook_url=cfg.webhook_url,
        update_types=MAX_WEBHOOK_UPDATE_TYPES,
        ca_bundle_configured=bool(cfg.ca_bundle),
        was_already_present=already_present,
        created=created,
        active_after=active_after,
        bot=_safe_bot_summary(me.payload, cfg=cfg),
        provider_statuses={
            "me": int(me.status),
            "subscriptions_before": int(before.status),
            "subscription_create": create_status,
            "subscriptions_after": int(after.status),
        },
    )


def _redacted_error_code(error_code: str, *, cfg: MaxWebhookConfig | None) -> str:
    safe = str(error_code or "registration_failed")
    if cfg is not None:
        for secret in (cfg.token, cfg.secret):
            if secret:
                safe = safe.replace(secret, "redacted")
    return _ERROR_CODE_RE.sub("_", safe)[:160].strip("_") or "registration_failed"


def _error_report(
    cfg: MaxWebhookConfig | None,
    *,
    mode: str,
    error_code: str,
    network_called: bool,
) -> MaxWebhookReport:
    return MaxWebhookReport(
        ok=False,
        mode=mode,
        applied=False,
        network_called=bool(network_called),
        api_base_url=cfg.api_base_url if cfg else MAX_PLATFORM_API_BASE_URL,
        webhook_url=cfg.webhook_url if cfg else "",
        update_types=MAX_WEBHOOK_UPDATE_TYPES,
        ca_bundle_configured=bool(cfg and cfg.ca_bundle),
        error_code=_redacted_error_code(error_code, cfg=cfg),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or apply the official MAX webhook subscription")
    parser.add_argument("--apply", action="store_true", help="Contact MAX and create/verify the subscription")
    parser.add_argument("--timeout-sec", type=int, default=30)
    args = parser.parse_args()

    mode = "apply" if bool(args.apply) else "dry_run"
    cfg: MaxWebhookConfig | None = None
    try:
        cfg = _validated_config()
        report = (
            _apply_registration(cfg, timeout_sec=int(args.timeout_sec))
            if args.apply
            else _dry_run_report(cfg)
        )
    except MaxWebhookRegistrationError as exc:
        report = _error_report(
            cfg,
            mode=mode,
            error_code=exc.code,
            network_called=exc.network_called,
        )
    except ValueError as exc:
        report = _error_report(
            cfg,
            mode=mode,
            error_code=f"invalid_argument:{type(exc).__name__}",
            network_called=False,
        )

    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))
    return 0 if report.ok else 2


if __name__ == "__main__":
    sys.exit(main())
