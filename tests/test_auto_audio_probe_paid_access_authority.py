from __future__ import annotations

from scripts.probe_auto_audio_dry_run import (
    PROBE_SOURCE,
    _cleanup_probe_rows,
    _ensure_probe_user,
    _grant_probe_access,
)
from services.db import db
from services.subscription import has_access


TOKEN_USER_ID = -910_000_291
LEGACY_USER_ID = -910_000_292


def _count(sql: str, params: tuple[object, ...]) -> int:
    with db() as conn:
        row = conn.execute(sql, params).fetchone()
    return int(row[0] if row is not None else 0)


def test_probe_grants_access_through_authoritative_token_wallet(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ECONOMY_ENABLED", "1")
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "hard")
    _cleanup_probe_rows(user_id=TOKEN_USER_ID)

    try:
        _ensure_probe_user(user_id=TOKEN_USER_ID)
        backend = _grant_probe_access(
            user_id=TOKEN_USER_ID,
            run_id="test-auto-audio-token-authority",
        )

        assert backend == "practice_tokens"
        assert has_access(TOKEN_USER_ID, "morning") is True
        assert _count(
            "SELECT COUNT(*) FROM practice_wallets WHERE user_id=? AND available_tokens=1",
            (TOKEN_USER_ID,),
        ) == 1
        assert _count(
            "SELECT COUNT(*) FROM practice_ledger WHERE user_id=? AND source=?",
            (TOKEN_USER_ID, PROBE_SOURCE),
        ) == 1
        assert _count(
            "SELECT COUNT(*) FROM subscriptions WHERE user_id=?",
            (TOKEN_USER_ID,),
        ) == 0
    finally:
        _cleanup_probe_rows(user_id=TOKEN_USER_ID)


def test_probe_keeps_legacy_subscription_fallback_when_tokens_are_not_authoritative(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ECONOMY_ENABLED", "1")
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "off")
    _cleanup_probe_rows(user_id=LEGACY_USER_ID)

    try:
        _ensure_probe_user(user_id=LEGACY_USER_ID)
        backend = _grant_probe_access(
            user_id=LEGACY_USER_ID,
            run_id="test-auto-audio-legacy-authority",
        )

        assert backend == "legacy_subscription"
        assert has_access(LEGACY_USER_ID, "morning") is True
        assert _count(
            "SELECT COUNT(*) FROM subscriptions WHERE user_id=? AND status='active'",
            (LEGACY_USER_ID,),
        ) == 1
        assert _count(
            "SELECT COUNT(*) FROM practice_wallets WHERE user_id=?",
            (LEGACY_USER_ID,),
        ) == 0
    finally:
        _cleanup_probe_rows(user_id=LEGACY_USER_ID)


def test_probe_cleanup_removes_canonical_token_and_account_artifacts(monkeypatch) -> None:
    monkeypatch.setenv("TOKEN_ECONOMY_ENABLED", "1")
    monkeypatch.setenv("TOKEN_ENFORCEMENT_MODE", "hard")
    _cleanup_probe_rows(user_id=TOKEN_USER_ID)

    _ensure_probe_user(user_id=TOKEN_USER_ID)
    _grant_probe_access(
        user_id=TOKEN_USER_ID,
        run_id="test-auto-audio-cleanup",
    )
    touched = _cleanup_probe_rows(user_id=TOKEN_USER_ID)

    assert touched >= 4
    for table, column in (
        ("practice_wallets", "user_id"),
        ("practice_ledger", "user_id"),
        ("accounts", "account_id"),
        ("users", "user_id"),
    ):
        assert _count(
            f"SELECT COUNT(*) FROM {table} WHERE {column}=?",
            (TOKEN_USER_ID,),
        ) == 0
