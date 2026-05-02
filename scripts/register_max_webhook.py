from __future__ import annotations

"""Register the canonical MAX webhook subscription.

This script is intentionally small and dependency-free so it can be run on the
server after deploying environment variables. It does not start the bot and it
never prints secrets.

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
import urllib.request
from dataclasses import dataclass
from typing import Any


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
        api_base_url=_env("MAX_API_BASE_URL", "https://platform-api.max.ru").rstrip("/"),
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
    if not cfg.public_base_url.startswith("https://"):
        raise SystemExit("MESSENGER_PUBLIC_BASE_URL must start with https:// for MAX production webhook")
    return cfg


def _json_request(url: str, *, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def main() -> int:
    cfg = _load_config()
    payload = {
        "url": cfg.webhook_url,
        "update_types": ["message_created", "message_callback", "bot_started"],
        "secret": cfg.secret,
    }
    data = _json_request(cfg.api_base_url + "/subscriptions", token=cfg.token, payload=payload)
    success = bool(data.get("success", True))
    print(json.dumps({"success": success, "webhook_url": cfg.webhook_url, "response": data}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
