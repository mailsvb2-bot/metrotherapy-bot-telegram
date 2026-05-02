from __future__ import annotations

"""Register and verify the canonical MAX webhook subscription.

This script is intentionally dependency-free so it can be run on the server
after deploying environment variables. It does not start the bot and it never
prints secrets.

Required env:
  MAX_BOT_TOKEN
  MAX_WEBHOOK_SECRET
  MESSENGER_PUBLIC_BASE_URL

Example:
  APP_ENV=prod python -m scripts.register_max_webhook
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


MAX_PLATFORM_API_BASE_URL = "https://platform-api.max.ru"
MAX_WEBHOOK_UPDATE_TYPES = ("message_created", "message_callback", "bot_started")


@dataclass(frozen=True)
class MaxWebhookConfig:
    api_base_url: str
    token: str
    public_base_url: str
    secret: str

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
    )
    missing = []
    if not cfg.token:
        missing.append("MAX_BOT_TOKEN")
    if not cfg.public_base_url:
        missing.append("MESSENGER_PUBLIC_BASE_URL")
    if not cfg.secret:
        missing.append("MAX_WEBHOOK_SECRET")
    if missing:
        raise SystemExit("Missing required environment variables: " + ", ".join(missing))
    if "botapi.max.ru" in cfg.api_base_url:
        raise SystemExit("MAX_API_BASE_URL must use https://platform-api.max.ru, not legacy botapi.max.ru")
    if not cfg.api_base_url.startswith(MAX_PLATFORM_API_BASE_URL):
        raise SystemExit("MAX_API_BASE_URL must start with https://platform-api.max.ru")
    if not cfg.public_base_url.startswith("https://"):
        raise SystemExit("MESSENGER_PUBLIC_BASE_URL must start with https:// for MAX production webhook")
    return cfg


def _json_request(
    url: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None
    headers = {"Authorization": token}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
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
    subscriptions = data.get("subscriptions") or []
    urls: list[str] = []
    if not isinstance(subscriptions, list):
        return urls
    for item in subscriptions:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
    return urls


def main() -> int:
    cfg = _load_config()

    me = _json_request(cfg.api_base_url + "/me", token=cfg.token, method="GET")
    before = _json_request(cfg.api_base_url + "/subscriptions", token=cfg.token, method="GET")
    existing_urls = _subscription_urls(before)

    payload = {
        "url": cfg.webhook_url,
        "update_types": list(MAX_WEBHOOK_UPDATE_TYPES),
        "secret": cfg.secret,
    }
    created = _json_request(cfg.api_base_url + "/subscriptions", token=cfg.token, method="POST", payload=payload)
    after = _json_request(cfg.api_base_url + "/subscriptions", token=cfg.token, method="GET")
    active_urls = _subscription_urls(after)

    # MAX supports Webhook and Long Polling as alternative delivery modes.
    # This script only configures Webhook; it never calls /updates.
    success = bool(created.get("success", True)) and cfg.webhook_url in active_urls
    output = {
        "success": success,
        "mode": "webhook",
        "api_base_url": cfg.api_base_url,
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
