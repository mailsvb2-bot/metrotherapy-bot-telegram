from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "scripts" / "probe_auto_audio_dry_run.py"


def test_auto_audio_probe_uses_current_paid_access_authority() -> None:
    text = PROBE.read_text(encoding="utf-8")

    assert "token_access_authoritative()" in text
    assert "grant_tokens(" in text
    assert 'return "practice_tokens"' in text
    assert 'return "legacy_subscription"' in text
    assert "synthetic subscription did not grant access" not in text
    assert 'ProbeInvariantError("paid_access_not_visible")' in text
    assert '"access_backend": access_backend' in text


def test_auto_audio_probe_cleans_token_wallet_artifacts() -> None:
    text = PROBE.read_text(encoding="utf-8")

    for table in (
        "practice_reservations",
        "payment_token_grants",
        "practice_ledger",
        "user_practice_preferences",
        "practice_wallets",
        "account_channel_identities",
        "accounts",
    ):
        assert f"DELETE FROM {table}" in text
