from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from services import growth_apply_gateway
from services.growth_apply_gateway_core import ApplyPolicy, evaluate_apply_policy, next_status
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


def test_default_policy_blocks_all_execution_paths():
    result = evaluate_apply_policy(
        action_type="budget_change",
        payload={"delta_minor": 100, "delta_pct": 1, "risk": "low"},
        requester_id=1,
        approver_id=2,
        policy=ApplyPolicy(),
    )
    assert result["mode"] == "approval_only"
    assert result["dispatch_allowed"] is False
    assert result["policy_passed"] is False
    assert "global_kill_switch_enabled" in result["violations"]
    assert "budget_changes_disabled" in result["violations"]


def test_self_approval_is_rejected_even_when_other_limits_allow():
    result = evaluate_apply_policy(
        action_type="campaign_pause",
        payload={"risk": "low"},
        requester_id=7,
        approver_id=7,
        policy=ApplyPolicy(
            allow_pause_resume=True,
            require_distinct_approver=True,
            kill_switch_enabled=False,
        ),
    )
    assert result["policy_passed"] is False
    assert "requester_cannot_self_approve" in result["violations"]


def test_state_machine_does_not_allow_non_pending_transition():
    with pytest.raises(ValueError, match="apply_request_not_pending"):
        next_status(current_status="approved", decision="reject", policy_passed=True)


def test_request_is_idempotent_and_database_hard_locks_dispatch(tmp_path, monkeypatch):
    path = tmp_path / "gateway.db"
    _prepare(path)
    monkeypatch.setattr(growth_apply_gateway, "db", lambda: _fake_db(path))

    kwargs = {
        "action_type": "creative_rotate",
        "target_platform": "yandex_ads",
        "target_ref": "campaign:42",
        "payload": {"creative_id": "new-1", "risk": "medium"},
        "requested_by": 100,
    }
    first = growth_apply_gateway.create_apply_request(**kwargs)
    second = growth_apply_gateway.create_apply_request(**kwargs)

    assert first["id"] == second["id"]
    with _fake_db(path) as conn:
        request = conn.execute("SELECT mode, dispatch_allowed, status FROM growth_apply_requests").fetchone()
        audit_count = conn.execute("SELECT COUNT(*) AS n FROM growth_apply_audit").fetchone()["n"]
    assert request["mode"] == "approval_only"
    assert request["dispatch_allowed"] == 0
    assert request["status"] == "pending_review"
    assert audit_count == 1


def test_rejection_is_atomic_and_audited(tmp_path, monkeypatch):
    path = tmp_path / "reject.db"
    _prepare(path)
    monkeypatch.setattr(growth_apply_gateway, "db", lambda: _fake_db(path))
    row = growth_apply_gateway.create_apply_request(
        action_type="campaign_pause",
        target_platform="vk_ads",
        target_ref="campaign:9",
        payload={"risk": "high"},
        requested_by=1,
    )

    decided = growth_apply_gateway.decide_apply_request(
        request_id=int(row["id"]),
        decision="reject",
        decided_by=2,
        reason="insufficient evidence",
    )

    assert decided["status"] == "rejected"
    assert decided["dispatch_allowed"] == 0
    with _fake_db(path) as conn:
        events = conn.execute(
            "SELECT event_type, before_status, after_status FROM growth_apply_audit ORDER BY id"
        ).fetchall()
    assert [(r["event_type"], r["before_status"], r["after_status"]) for r in events] == [
        ("request_created", None, "pending_review"),
        ("request_rejected", "pending_review", "rejected"),
    ]


def test_approval_requires_policy_and_distinct_approver(tmp_path, monkeypatch):
    path = tmp_path / "approve.db"
    _prepare(path)
    monkeypatch.setattr(growth_apply_gateway, "db", lambda: _fake_db(path))
    monkeypatch.setattr(
        growth_apply_gateway,
        "current_apply_policy",
        lambda: ApplyPolicy(
            allow_pause_resume=True,
            require_distinct_approver=True,
            kill_switch_enabled=False,
        ),
    )
    row = growth_apply_gateway.create_apply_request(
        action_type="campaign_pause",
        target_platform="yandex_ads",
        target_ref="campaign:77",
        payload={"risk": "low"},
        requested_by=10,
    )

    with pytest.raises(ValueError, match="apply_policy_blocked"):
        growth_apply_gateway.decide_apply_request(
            request_id=int(row["id"]),
            decision="approve",
            decided_by=10,
            reason="self approve",
        )

    approved = growth_apply_gateway.decide_apply_request(
        request_id=int(row["id"]),
        decision="approve",
        decided_by=11,
        reason="reviewed",
    )
    assert approved["status"] == "approved"
    assert approved["dispatch_allowed"] == 0


def test_database_rejects_dispatch_enable_even_after_approval(tmp_path):
    path = tmp_path / "db_lock.db"
    _prepare(path)
    with _fake_db(path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO growth_apply_requests(
                    request_key, action_type, target_platform, target_ref,
                    payload_json, policy_json, status, mode, dispatch_allowed,
                    requested_by, requested_at
                ) VALUES('k','campaign_pause','x','y','{}','{}','approved','approval_only',1,1,'now')
                """
            )


def test_report_degrades_when_optional_schema_is_missing(tmp_path, monkeypatch):
    path = tmp_path / "missing.db"
    monkeypatch.setattr(growth_apply_gateway, "db", lambda: _fake_db(path))
    text = growth_apply_gateway.build_apply_gateway_report()
    assert "DEGRADED" in text
    assert "dispatch_allowed=False" in text
