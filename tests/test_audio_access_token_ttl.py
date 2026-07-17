from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.messenger import audio_access


def test_audio_access_token_expires_after_configured_ttl(monkeypatch):
    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "6")
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    created_at = (now - timedelta(hours=6, seconds=1)).isoformat()

    assert audio_access._grant_expired(created_at, now=now) is True


def test_audio_access_token_remains_valid_inside_ttl(monkeypatch):
    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "6")
    now = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    created_at = (now - timedelta(hours=5, minutes=59)).isoformat()

    assert audio_access._grant_expired(created_at, now=now) is False


def test_audio_access_token_ttl_has_safe_bounds(monkeypatch):
    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "0")
    assert audio_access._audio_access_ttl_hours() == 1

    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "9999")
    assert audio_access._audio_access_ttl_hours() == 12

    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "bad")
    assert audio_access._audio_access_ttl_hours() == 6


def test_audio_access_request_budget_has_safe_bounds(monkeypatch):
    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_MAX_REQUESTS", "0")
    assert audio_access._audio_access_max_requests() == 4

    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_MAX_REQUESTS", "9999")
    assert audio_access._audio_access_max_requests() == 256

    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_MAX_REQUESTS", "bad")
    assert audio_access._audio_access_max_requests() == 32


def test_audio_access_token_with_invalid_created_at_fails_closed():
    assert audio_access._grant_expired("not-a-date") is True
