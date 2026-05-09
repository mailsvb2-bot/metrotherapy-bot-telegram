from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from config.settings import settings
from services.ai.policy import ai_enabled_from_settings

log = logging.getLogger(__name__)


@dataclass
class OpenAIClient:
    api_key: str
    model: str = "gpt-4.1-mini"
    base_url: str = "https://api.openai.com/v1"
    timeout_sec: int = 20

    @classmethod
    def from_settings(cls) -> "OpenAIClient | None":
        if not ai_enabled_from_settings():
            return None

        key = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
        if not key:
            return None
        model = (getattr(settings, "OPENAI_MODEL", "") or "gpt-4.1-mini").strip() or "gpt-4.1-mini"
        base = (getattr(settings, "OPENAI_BASE_URL", "") or "https://api.openai.com/v1").strip() or "https://api.openai.com/v1"
        return cls(api_key=key, model=model, base_url=base)

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 300) -> str | None:
        """Minimal Chat Completions call for admin/marketing helper use.

        Returns assistant text, or None on API/transport/parse failure.
        """
        url = self.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            try:
                exc.read()
            except OSError:
                pass
            log.warning("openai_chat_http_error", extra={"status": getattr(exc, "code", None)})
            return None
        except TimeoutError:
            log.warning("openai_chat_timeout")
            return None
        except (urllib.error.URLError, ConnectionError, OSError) as exc:
            log.warning("openai_chat_transport_error", extra={"error_type": type(exc).__name__})
            return None

        try:
            obj: dict[str, Any] = json.loads(raw)
            choices = obj.get("choices") or []
            if not choices:
                log.warning("openai_chat_empty_choices")
                return None
            msg = (choices[0] or {}).get("message") or {}
            text = (msg.get("content") or "").strip()
            return text or None
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            log.warning("openai_chat_bad_response", extra={"error_type": type(exc).__name__})
            return None

    async def achat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 300) -> str | None:
        """Async-safe wrapper around the blocking stdlib HTTP call."""
        return await asyncio.to_thread(self.chat, messages, temperature=temperature, max_tokens=max_tokens)
