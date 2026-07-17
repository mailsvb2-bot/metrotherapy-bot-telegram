from __future__ import annotations

from pathlib import Path

from ops import deploy_webhook_hardened as hardened


def test_deploy_webhook_rejects_missing_invalid_and_oversized_bodies(monkeypatch) -> None:
    monkeypatch.setattr(hardened, "MAX_BODY_BYTES", 1024)

    assert hardened._parse_content_length(None) == (None, 411)
    assert hardened._parse_content_length("broken") == (None, 400)
    assert hardened._parse_content_length("0") == (None, 400)
    assert hardened._parse_content_length("1025") == (None, 413)
    assert hardened._parse_content_length("1024") == (1024, None)


def test_delivery_replay_cache_is_atomic_bounded_and_expires() -> None:
    cache = hardened._DeliveryReplayCache(max_items=2, ttl_sec=10)

    assert cache.claim("delivery-a", now=100.0) is True
    assert cache.claim("delivery-a", now=101.0) is False
    assert cache.claim("delivery-b", now=102.0) is True
    assert cache.claim("delivery-c", now=103.0) is True
    # Oldest entry was evicted by the hard size bound.
    assert cache.claim("delivery-a", now=104.0) is True

    expiring = hardened._DeliveryReplayCache(max_items=5, ttl_sec=10)
    assert expiring.claim("delivery-x", now=100.0) is True
    assert expiring.claim("delivery-x", now=111.0) is True
    expiring.release("delivery-x")
    assert expiring.claim("delivery-x", now=112.0) is True


def test_trigger_replay_is_detected_from_bounded_deploy_log(monkeypatch) -> None:
    sha = "a" * 40
    monkeypatch.setattr(
        hardened.legacy,
        "_read_log_tail",
        lambda _path: ([f"=== deploy trigger sha: {sha} ==="], "2026-07-17T00:00:00Z"),
    )

    assert hardened._trigger_already_observed(sha) is True
    assert hardened._trigger_already_observed("b" * 40) is False


def test_installer_uses_hardened_runtime_and_server_is_threaded() -> None:
    installer = Path("scripts/install_github_deploy_webhook_service.sh").read_text(encoding="utf-8")
    source = Path("ops/deploy_webhook_hardened.py").read_text(encoding="utf-8")

    assert 'HOOK_SOURCE="$APP_DIR/ops/deploy_webhook_hardened.py"' in installer
    assert "ThreadingHTTPServer" in source
    assert "GITHUB_WEBHOOK_MAX_BODY_BYTES" in source
    assert "X-GitHub-Delivery" in source
    assert "_REPLAY_CACHE.release(delivery_id)" in source
