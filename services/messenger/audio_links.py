from __future__ import annotations

import urllib.parse
from pathlib import Path

from config.settings import settings
from services.catalog import FULL_DIR


AUDIO_MEDIA_PREFIX = '/media/audio/full/'
AUDIO_ACCESS_PREFIX = '/media/audio/access/'


def build_public_audio_url(path: str | Path) -> str:
    base = (getattr(settings, 'MESSENGER_PUBLIC_BASE_URL', '') or '').strip().rstrip('/')
    if not base:
        return ''
    p = Path(path)
    try:
        filename = p.name
    except TypeError:
        return ''
    if not filename:
        return ''
    return f"{base}{AUDIO_MEDIA_PREFIX}{urllib.parse.quote(filename)}"



def resolve_public_audio_path(filename: str) -> Path | None:
    raw = Path(urllib.parse.unquote(filename)).name
    if not raw:
        return None
    candidate = FULL_DIR / raw
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate



def build_audio_access_url(token: str) -> str:
    base = (getattr(settings, 'MESSENGER_PUBLIC_BASE_URL', '') or '').strip().rstrip('/')
    raw = (token or '').strip()
    if not base or not raw:
        return ''
    return f"{base}{AUDIO_ACCESS_PREFIX}{urllib.parse.quote(raw)}"
