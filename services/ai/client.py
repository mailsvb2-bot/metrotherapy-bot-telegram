from __future__ import annotations
import logging


import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any

from config.settings import settings


@dataclass
class OpenAIClient:
    api_key: str
    model: str = "gpt-4.1-mini"
    base_url: str = "https://api.openai.com/v1"
    timeout_sec: int = 20

    @classmethod
    def from_settings(cls) -> "OpenAIClient | None":
        key = (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
        if not key:
            return None
        model = (getattr(settings, "OPENAI_MODEL", "") or "gpt-4.1-mini").strip() or "gpt-4.1-mini"
        base = (getattr(settings, "OPENAI_BASE_URL", "") or "https://api.openai.com/v1").strip() or "https://api.openai.com/v1"
        return cls(api_key=key, model=model, base_url=base)

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.3, max_tokens: int = 300) -> str | None:
        """Минимальный Chat Completions вызов.

        Возвращает текст ассистента или None при ошибке.
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
        except urllib.error.HTTPError as e:
            try:
                _ = e.read()
            except OSError:
                logging.getLogger(__name__).exception("Unhandled exception")
            return None
        except (TimeoutError, urllib.error.URLError, ConnectionError):
            logging.getLogger(__name__).exception("Unhandled exception")
            return None
        except OSError:
            logging.getLogger(__name__).exception("Unhandled exception")
            return None

        try:
            obj: dict[str, Any] = json.loads(raw)
            choices = obj.get("choices") or []
            if not choices:
                return None
            msg = (choices[0] or {}).get("message") or {}
            text = (msg.get("content") or "").strip()
            return text or None
        except (json.JSONDecodeError, KeyError, IndexError):
            logging.getLogger(__name__).exception("Unhandled exception")
            return None
        except TypeError:
            logging.getLogger(__name__).exception("Unhandled exception")
            return None
