from __future__ import annotations

from datetime import timedelta

import pytest

from core.time_utils import utc_now
from services.messenger import media_assets


def test_media_token_ttl_is_provider_specific_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MESSENGER_MEDIA_TOKEN_MAX_AGE_SEC", "100")
    monkeypatch.setenv("MAX_MEDIA_TOKEN_MAX_AGE_SEC", "25")
    assert media_assets._token_max_age_sec("max") == 25
    assert media_assets._token_max_age_sec("vk") == 100

    monkeypatch.setenv("MAX_MEDIA_TOKEN_MAX_AGE_SEC", "broken")
    assert media_assets._token_max_age_sec("max") == media_assets._DEFAULT_TOKEN_MAX_AGE_SEC

    monkeypatch.setenv("MAX_MEDIA_TOKEN_MAX_AGE_SEC", str(10**12))
    assert media_assets._token_max_age_sec("max") == 365 * 24 * 60 * 60


def test_media_token_expiry_uses_issuance_time_not_last_use() -> None:
    old = (utc_now() - timedelta(seconds=61)).isoformat()
    fresh = (utc_now() - timedelta(seconds=5)).isoformat()

    assert media_assets._token_expired(old, max_age_sec=60) is True
    assert media_assets._token_expired(fresh, max_age_sec=60) is False
    assert media_assets._token_expired("not-a-date", max_age_sec=60) is True
    assert media_assets._token_expired(old, max_age_sec=0) is False


def test_empty_remote_media_token_is_rejected(tmp_path) -> None:
    source = tmp_path / "audio.mp3"
    source.write_bytes(b"audio")

    with pytest.raises(ValueError, match="remote media token is required"):
        media_assets.store_media_token("max", source, "", media_type="audio")
