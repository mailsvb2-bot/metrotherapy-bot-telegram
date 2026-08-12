from __future__ import annotations

from pathlib import Path

import pytest

from scripts import probe_postgres_messenger_outbox as probe
from services.messenger import delivery_pool
from services.probe_safety import ProbeMutationAuthorizationRequired


ROOT = Path(__file__).resolve().parents[1]
PROBE_SCRIPT = ROOT / "scripts" / "probe_postgres_messenger_outbox.py"


def test_probe_platform_is_outside_live_delivery_worker_namespace() -> None:
    assert probe.PROBE_PLATFORM not in {"vk", "max"}
    assert probe.PROBE_PLATFORM not in delivery_pool._ALLOWED_PLATFORMS  # noqa: SLF001
    with pytest.raises(ValueError, match="unsupported delivery platform"):
        delivery_pool.claim_stream_head(platform=probe.PROBE_PLATFORM)


def test_probe_requires_live_db_authorization_before_database_access() -> None:
    with pytest.raises(ProbeMutationAuthorizationRequired):
        probe.run_probe(allow_live_db_mutation=False)


def test_probe_reuses_production_postgres_stream_head_sql_without_live_queue_helpers() -> None:
    source = PROBE_SCRIPT.read_text(encoding="utf-8")

    assert "delivery_pool._POSTGRES_STREAM_HEAD_SQL" in source
    assert "delivery_pool._claimed_from_row" in source
    assert "persist_reply_bundle" not in source
    assert "claim_inbound_event" not in source
    assert 'PROBE_PLATFORM = "__probe_postgres_messenger_outbox__"' in source
    assert "require_live_db_mutation" in source


def test_probe_cleanup_is_scoped_to_reserved_platform_and_unique_prefix() -> None:
    source = PROBE_SCRIPT.read_text(encoding="utf-8")
    cleanup_start = source.index("def _cleanup")
    cleanup_end = source.index("\ndef _insert_probe_row", cleanup_start)
    cleanup = source[cleanup_start:cleanup_end]

    assert "DELETE FROM messenger_delivery_outbox WHERE platform=? AND event_key LIKE ?" in cleanup
    assert "PROBE_PLATFORM" in cleanup
    assert 'f"{key_prefix}%"' in cleanup
