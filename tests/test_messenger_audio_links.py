from pathlib import Path

from config.settings import settings
from services.messenger.audio_links import build_public_audio_url, build_audio_access_url


def test_build_public_audio_url_uses_filename_only(monkeypatch):
    monkeypatch.setattr(settings, 'MESSENGER_PUBLIC_BASE_URL', 'https://example.test')
    url = build_public_audio_url(Path('/tmp/anything/1_morning.opus'))
    assert url == 'https://example.test/media/audio/full/1_morning.opus'


def test_build_audio_access_url(monkeypatch):
    monkeypatch.setattr(settings, 'MESSENGER_PUBLIC_BASE_URL', 'https://example.test/')
    url = build_audio_access_url('abc123')
    assert url == 'https://example.test/media/audio/access/abc123'
