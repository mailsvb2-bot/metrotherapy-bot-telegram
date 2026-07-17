from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.time_utils import utc_now
from runtime import messenger_media_http
from services.db import db
from services.messenger import audio_access


def test_audio_access_request_budget_is_atomic_and_finite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_TTL_HOURS", "6")
    monkeypatch.setenv("AUDIO_ACCESS_TOKEN_MAX_REQUESTS", "4")
    token = "request-budget-test-token"
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"audio")

    with db() as conn:
        conn.execute("DELETE FROM user_audio_access_tokens WHERE token=?", (token,))
        conn.execute(
            """
            INSERT INTO user_audio_access_tokens(
                token, user_id, sequence_key, anchor, title, file_path,
                platform, created_at, access_count
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """.strip(),
            (
                token,
                991001,
                "full_series",
                1,
                "Test audio",
                str(audio_path),
                "vk",
                utc_now().replace(microsecond=0).isoformat(),
                0,
            ),
        )

    for expected_count in range(1, 5):
        grant = audio_access.register_audio_access(token)
        assert grant is not None
        assert grant.access_count == expected_count

    assert audio_access.get_audio_access_grant(token) is None
    assert audio_access.register_audio_access(token) is None

    with db() as conn:
        row = conn.execute(
            "SELECT access_count FROM user_audio_access_tokens WHERE token=?",
            (token,),
        ).fetchone()
        assert int(row["access_count"]) == 4
        conn.execute("DELETE FROM user_audio_access_tokens WHERE token=?", (token,))


@pytest.mark.asyncio
async def test_protected_audio_response_disables_caching(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "protected.mp3"
    audio_path.write_bytes(b"audio")
    grant = audio_access.AudioAccessGrant(
        token="protected-token",
        user_id=991002,
        sequence_key="full_series",
        anchor=1,
        title="Protected",
        file_path=audio_path,
        platform="max",
        created_at=utc_now().replace(microsecond=0).isoformat(),
        first_accessed_at=None,
        access_count=1,
    )
    monkeypatch.setattr(messenger_media_http, "get_audio_access_grant", lambda _token: grant)
    monkeypatch.setattr(messenger_media_http, "register_audio_access", lambda _token: grant)
    request = SimpleNamespace(match_info={"token": grant.token})

    response = await messenger_media_http.audio_access(request)

    assert response.headers["Cache-Control"] == "private, no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
