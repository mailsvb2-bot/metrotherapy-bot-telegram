from __future__ import annotations

from pathlib import Path

from scripts.audit_messenger_audio_delivery import _parse_platforms


def test_batch_audio_audit_accepts_only_user_messenger_platforms():
    assert _parse_platforms("vk,max") == ("vk", "max")
    assert _parse_platforms("vk") == ("vk",)
    assert _parse_platforms("max") == ("max",)


def test_batch_audio_audit_rejects_telegram_and_unknown_platforms():
    try:
        _parse_platforms("telegram")
    except ValueError as exc:
        assert "unsupported_platforms" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("telegram should not be a VK/MAX batch target")


def test_batch_audio_audit_uses_canonical_delivery_path_not_direct_sender_loop():
    source = Path("scripts/audit_messenger_audio_delivery.py").read_text(encoding="utf-8")

    assert "send_next_audio_to_user" in source
    assert "build_delivery_plan" in source
    assert "user_channel_identities" in source
    assert "send_audio_file(" not in source
    assert "VK_AUDIO_PROBE_USER_ID" not in source
    assert "MAX_AUDIO_PROBE_USER_ID" not in source


def test_single_audio_probe_remains_canary_not_batch_delivery():
    source = Path("scripts/probe_messenger_audio_delivery.py").read_text(encoding="utf-8")

    assert "VK_AUDIO_PROBE_USER_ID" in source
    assert "MAX_AUDIO_PROBE_USER_ID" in source
    assert "send_audio_file" in source
