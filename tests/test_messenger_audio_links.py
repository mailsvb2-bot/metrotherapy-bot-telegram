from pathlib import Path

from config.settings import settings
from services.messenger.audio_links import build_public_audio_url, build_audio_access_url, public_full_audio_urls_enabled


def test_build_public_audio_url_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv('ALLOW_PUBLIC_FULL_AUDIO_URLS', raising=False)
    monkeypatch.setenv('APP_ENV', 'dev')
    monkeypatch.setattr(settings, 'MESSENGER_PUBLIC_BASE_URL', 'https://example.test')
    assert build_public_audio_url(Path('/tmp/anything/1_morning.opus')) == ''


def test_build_public_audio_url_uses_filename_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'dev')
    monkeypatch.setenv('ALLOW_PUBLIC_FULL_AUDIO_URLS', '1')
    monkeypatch.setattr(settings, 'MESSENGER_PUBLIC_BASE_URL', 'https://example.test')
    url = build_public_audio_url(Path('/tmp/anything/1_morning.opus'))
    assert url == 'https://example.test/media/audio/full/1_morning.opus'


def test_public_audio_url_flag_is_ignored_in_prod(monkeypatch):
    monkeypatch.setenv('APP_ENV', 'prod')
    monkeypatch.setenv('ALLOW_PUBLIC_FULL_AUDIO_URLS', '1')
    assert public_full_audio_urls_enabled() is False


def test_build_audio_access_url(monkeypatch):
    monkeypatch.setattr(settings, 'MESSENGER_PUBLIC_BASE_URL', 'https://example.test/')
    url = build_audio_access_url('abc123')
    assert url == 'https://example.test/media/audio/access/abc123'
