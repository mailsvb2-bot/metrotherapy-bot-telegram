from __future__ import annotations
import logging
import urllib.error


import random
from pathlib import Path
from urllib.request import urlopen, Request
import json

from services.cache import cache


PROJECT_ROOT = Path(__file__).resolve().parents[1]
QUOTES_FILE = PROJECT_ROOT / "data" / "quotes.txt"


def _read_local_quotes() -> list[str]:
    if not QUOTES_FILE.exists():
        return []
    raw = QUOTES_FILE.read_text(encoding="utf-8", errors="ignore")
    # поддерживаем разделители: "—", "---" и пустые строки между блоками
    blocks: list[str] = []
    buf: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s in ("—", "---", "***"):
            if buf:
                blocks.append("\n".join(buf).strip())
                buf = []
            continue
        buf.append(s)
    if buf:
        blocks.append("\n".join(buf).strip())
    return [b for b in blocks if b]


def _fetch_zenquote(timeout: float = 4.0) -> str | None:
    """Fallback: zenquotes.io (free). Возвращаем короткую цитату без длинных ответов."""
    try:
        req = Request("https://zenquotes.io/api/random", headers={"User-Agent": "metra-bot"})
        with urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", errors="ignore"))
        if isinstance(data, list) and data:
            q = str(data[0].get("q") or "").strip()
            a = str(data[0].get("a") or "").strip()
            if q and a:
                return f"{q}\n— {a}"
            if q:
                return q
    except (urllib.error.URLError, TimeoutError, UnicodeDecodeError):
        logging.getLogger(__name__).exception("quote fetch failed")
        return None
    except (json.JSONDecodeError, KeyError, IndexError):
        logging.getLogger(__name__).exception("quote fetch failed")
        return None
    return None


def get_quote(ttl_sec: int = 300) -> str:
    """Offline-first цитата.

    1) Локальный файл data/quotes.txt
    2) Fallback: zenquotes.io
    """
    cached = cache.get("quote") if hasattr(cache, "get") else None
    if cached:
        return str(cached)

    quotes = _read_local_quotes()
    text = random.choice(quotes) if quotes else None
    if not text:
        text = _fetch_zenquote() or "Когда Вы замедляетесь — жизнь догоняет Вас."

    if hasattr(cache, "set"):
        cache.set("quote", text, ttl=ttl_sec)
    return text