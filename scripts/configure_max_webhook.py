from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MaxWebhookConfig:
    public_base_url: str
    webhook_secret: str
    bot_token: str
    set_webhook_url: str = ""
    method: str = "POST"
    token_header: str = "Authorization"
    auth_scheme: str = "raw"
    timeout_sec: float = 20.0


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _bool_env(name: str, default: str = "0") -> bool:
    return _env(name, default).lower() in {"1", "true", "yes", "on"}


def _append_query(url: str, params: dict[str, str]) -> str:
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query.update({k: v for k, v in params.items() if v})
    return urllib.parse.urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        urllib.parse.urlencode(query),
        parsed.fragment,
    ))


def build_max_webhook_url(public_base_url: str, webhook_secret: str) -> str:
    base = str(public_base_url or "").strip().rstrip("/")
    secret = str(webhook_secret or "").strip()
    if not base:
        raise ValueError("MESSENGER_PUBLIC_BASE_URL is required")
    if not base.startswith("https://"):
        raise ValueError("MESSENGER_PUBLIC_BASE_URL must start with https:// for MAX webhook")
    if not secret:
        raise ValueError("MAX_WEBHOOK_SECRET is required")
    return _append_query(f"{base}/webhooks/max", {"secret": secret})


def load_config_from_env() -> MaxWebhookConfig:
    return MaxWebhookConfig(
        public_base_url=_env("MESSENGER_PUBLIC_BASE_URL"),
        webhook_secret=_env("MAX_WEBHOOK_SECRET"),
        bot_token=_env("MAX_BOT_TOKEN"),
        set_webhook_url=_env("MAX_SET_WEBHOOK_URL"),
        method=_env("MAX_SET_WEBHOOK_METHOD", "POST").upper(),
        token_header=_env("MAX_SET_WEBHOOK_TOKEN_HEADER", "Authorization"),
        auth_scheme=_env("MAX_SET_WEBHOOK_AUTH_SCHEME", "raw").lower(),
        timeout_sec=float(_env("MAX_SET_WEBHOOK_TIMEOUT_SEC", "20") or 20),
    )


def authorization_value(config: MaxWebhookConfig) -> str:
    token = str(config.bot_token or "").strip()
    if not token:
        raise ValueError("MAX_BOT_TOKEN is required")
    if config.auth_scheme == "bearer":
        return f"Bearer {token}"
    if config.auth_scheme == "token":
        return f"Token {token}"
    return token


def build_registration_payload(webhook_url: str) -> dict[str, Any]:
    # Intentionally minimal and provider-agnostic. If MAX changes the exact field
    # name, override only the endpoint/transport contract, not runtime ingress.
    return {"url": webhook_url}


def register_max_webhook(config: MaxWebhookConfig, *, dry_run: bool = False) -> dict[str, Any]:
    webhook_url = build_max_webhook_url(config.public_base_url, config.webhook_secret)
    payload = build_registration_payload(webhook_url)
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "webhook_url": webhook_url,
            "set_webhook_url_configured": bool(config.set_webhook_url),
            "payload": payload,
        }

    if not config.set_webhook_url:
        raise ValueError(
            "MAX_SET_WEBHOOK_URL is required for --apply. "
            "Set it to the official MAX Bot API webhook registration endpoint for your bot."
        )

    headers = {
        "Content-Type": "application/json",
        str(config.token_header or "Authorization"): authorization_value(config),
    }
    request = urllib.request.Request(
        config.set_webhook_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method=config.method or "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(config.timeout_sec)) as response:
            raw = response.read().decode("utf-8")
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return {
            "ok": False,
            "dry_run": False,
            "status": int(exc.code),
            "webhook_url": webhook_url,
            "response": body[:4000],
        }

    parsed: Any
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = raw
    return {
        "ok": 200 <= status < 300,
        "dry_run": False,
        "status": status,
        "webhook_url": webhook_url,
        "response": parsed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure MAX webhook URL with MAX_WEBHOOK_SECRET.")
    parser.add_argument("--apply", action="store_true", help="Call MAX_SET_WEBHOOK_URL instead of dry-run output.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    args = parser.parse_args(argv)

    try:
        result = register_max_webhook(load_config_from_env(), dry_run=not args.apply)
    except (ValueError, OSError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("MAX webhook configuration result:")
        for key, value in result.items():
            if key == "webhook_url":
                # Do not leak the secret by default in human output.
                parsed = urllib.parse.urlsplit(str(value))
                query = urllib.parse.parse_qs(parsed.query)
                if "secret" in query:
                    value = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "secret=***MASKED***", parsed.fragment))
            print(f"{key}: {value}")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
