from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.messenger import audio_access


def test_audio_access_token_expires_after_configured_ttl(monkeypatch):
    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "24")
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    created_at = (now - timedelta(hours=24, seconds=1)).isoformat()

    assert audio_access._grant_expired(created_at, now=now) is True


def test_audio_access_token_remains_valid_inside_ttl(monkeypatch):
    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "24")
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    created_at = (now - timedelta(hours=23, minutes=59)).isoformat()

    assert audio_access._grant_expired(created_at, now=now) is False


def test_audio_access_token_ttl_has_safe_bounds(monkeypatch):
    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "0")
    assert audio_access._audio_access_ttl_hours() == 1

    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "9999")
    assert audio_access._audio_access_ttl_hours() == 24 * 7

    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "bad")
    assert audio_access._audio_access_ttl_hours() == 24


def test_audio_access_token_with_invalid_created_at_fails_closed():
    assert audio_access._grant_expired("not-a-date") is True
