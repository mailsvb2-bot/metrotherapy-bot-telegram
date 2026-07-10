from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from services import growth_apply_gateway
from services.migrations.growth_apply_gateway_v3 import apply as apply_gateway_migration


class _DbCtx:
    def __init__(self, path: Path):
        self.path = path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self):
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        assert self.conn is not None
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.conn.close()
        return False


def _fake_db(path: Path):
    return _DbCtx(path)


def _prepare(path: Path) -> None:
    with _fake_db(path) as conn:
        apply_gateway_migration(conn)


def test_invalid_numeric_env_fails_closed(monkeypatch):
    monkeypatch.setenv("GROWTH_APPLY_MAX_BUDGET_DELTA_MINOR", "not-a-number")
    monkeypatch.setenv("GROWTH_APPLY_MAX_BUDGET_DELTA_PCT", "bad")
    monkeypatch.setenv("GROWTH_APPLY_KILL_SWITCH", "1")

    policy = growth_apply_gateway.current_apply_policy()

    assert policy.max_abs_budget_delta_minor == 0
    assert policy.max_budget_delta_pct == 0.0
    assert policy.kill_switch_enabled is True


def test_expiry_transition_commits_before_error_is_returned(tmp_path, monkeypatch):
    path = tmp_path / "expired.db"
    _prepare(path)
    monkeypatch.setattr(growth_apply_gateway, "db", lambda: _fake_db(path))

    row = growth_apply_gateway.create_apply_request(
        action_type="campaign_pause",
        target_platform="yandex_ads",
        target_ref="campaign:expired",
        payload={"risk": "low"},
        requested_by=1,
    )
    expired_at = (growth_apply_gateway._utc_now() - timedelta(minutes=1)).isoformat()
    with _fake_db(path) as conn:
        conn.execute(
            "UPDATE growth_apply_requests SET expires_at=? WHERE id=?",
            (expired_at, int(row["id"])),
        )

    with pytest.raises(ValueError, match="growth_apply_request_expired"):
        growth_apply_gateway.decide_apply_request(
            request_id=int(row["id"]),
            decision="approve",
            decided_by=2,
            reason="late review",
        )

    with _fake_db(path) as conn:
        request = conn.execute(
            "SELECT status, decision_reason, dispatch_allowed FROM growth_apply_requests WHERE id=?",
            (int(row["id"]),),
        ).fetchone()
        audit = conn.execute(
            "SELECT event_type, after_status FROM growth_apply_audit WHERE request_id=? ORDER BY id DESC LIMIT 1",
            (int(row["id"]),),
        ).fetchone()

    assert request["status"] == "expired"
    assert request["decision_reason"] == "ttl_expired"
    assert request["dispatch_allowed"] == 0
    assert audit["event_type"] == "request_expired"
    assert audit["after_status"] == "expired"
