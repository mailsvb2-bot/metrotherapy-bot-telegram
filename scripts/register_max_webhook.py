from __future__ import annotations

"""Register and verify the MAX webhook subscription through the official API2.

The helper is dependency-free, never prints secrets, and uses the operating
system trust store unless MAX_CA_BUNDLE points to a dedicated PEM bundle.
"""

import json
import os
import re
import ssl
import sys
import urllib.request
from dataclasses import dataclass
from typing import Any


MAX_PLATFORM_API_BASE_URL = "https://platform-api2.max.ru"
MAX_WEBHOOK_UPDATE_TYPES = ("message_created", "message_callback", "bot_started")
_SECRET_RE = re.compile(r"[A-Za-z0-9_-]{5,256}")


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


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _load_config() -> MaxWebhookConfig:
    cfg = MaxWebhookConfig(
        api_base_url=_env("MAX_API_BASE_URL", MAX_PLATFORM_API_BASE_URL).rstrip("/"),
        token=_env("MAX_BOT_TOKEN"),
        public_base_url=_env("MESSENGER_PUBLIC_BASE_URL").rstrip("/"),
        secret=_env("MAX_WEBHOOK_SECRET", _env("MAX_SECRET_KEY")),
        ca_bundle=_env("MAX_CA_BUNDLE"),
    )
    missing: list[str] = []
    if not cfg.token:
        missing.append("MAX_BOT_TOKEN")
    if not cfg.public_base_url:
        missing.append("MESSENGER_PUBLIC_BASE_URL")
    if not cfg.secret:
        missing.append("MAX_WEBHOOK_SECRET")
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing))
    if not _SECRET_RE.fullmatch(cfg.secret):
        raise SystemExit("MAX_WEBHOOK_SECRET must match [A-Za-z0-9_-]{5,256}")
    if cfg.api_base_url != MAX_PLATFORM_API_BASE_URL:
        raise SystemExit("MAX_API_BASE_URL must be exactly https://platform-api2.max.ru")
    if not cfg.public_base_url.startswith("https://"):
        raise SystemExit("MESSENGER_PUBLIC_BASE_URL must start with https:// for MAX production webhook")
    if cfg.ca_bundle and not os.path.isfile(cfg.ca_bundle):
        raise SystemExit("MAX_CA_BUNDLE file does not exist")
    return cfg


def _ssl_context(ca_bundle: str = "") -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_bundle or None)


def _json_request(
    url: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    ca_bundle: str = "",
) -> dict[str, Any]:
    body = None
    headers = {"Authorization": token}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30, context=_ssl_context(ca_bundle)) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def _safe_bot_summary(me: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": me.get("user_id"),
        "username": me.get("username"),
        "name": me.get("name"),
        "is_bot": me.get("is_bot"),
    }


def _subscription_urls(data: dict[str, Any]) -> list[str]:
    subscriptions = data.get("subscriptions") or data.get("items") or data.get("data") or []
    urls: list[str] = []
    if not isinstance(subscriptions, list):
        return urls
    for item in subscriptions:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
    return urls


def main() -> int:
    cfg = _load_config()
    me = _json_request(cfg.api_base_url + "/me", token=cfg.token, method="GET", ca_bundle=cfg.ca_bundle)
    before = _json_request(
        cfg.api_base_url + "/subscriptions",
        token=cfg.token,
        method="GET",
        ca_bundle=cfg.ca_bundle,
    )
    existing_urls = _subscription_urls(before)

    payload = {
        "url": cfg.webhook_url,
        "update_types": list(MAX_WEBHOOK_UPDATE_TYPES),
        "secret": cfg.secret,
    }
    created = _json_request(
        cfg.api_base_url + "/subscriptions",
        token=cfg.token,
        method="POST",
        payload=payload,
        ca_bundle=cfg.ca_bundle,
    )
    after = _json_request(
        cfg.api_base_url + "/subscriptions",
        token=cfg.token,
        method="GET",
        ca_bundle=cfg.ca_bundle,
    )
    active_urls = _subscription_urls(after)

    success = bool(created.get("success", True)) and cfg.webhook_url in active_urls
    output = {
        "success": success,
        "mode": "webhook",
        "api_base_url": cfg.api_base_url,
        "ca_bundle_configured": bool(cfg.ca_bundle),
        "bot": _safe_bot_summary(me),
        "webhook_url": cfg.webhook_url,
        "was_already_present": cfg.webhook_url in existing_urls,
        "active_subscription_urls": active_urls,
        "response": created,
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
