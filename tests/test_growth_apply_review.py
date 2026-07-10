from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from services import growth_apply_gateway, growth_apply_review
from services.growth_apply_gateway_core import ApplyPolicy
from services.migrations.growth_apply_gateway_v3 import apply as apply_gateway_migration
from services.migrations.growth_apply_review_confirmations_v4 import apply as apply_confirmation_migration


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
        apply_confirmation_migration(conn)


def _patch_db(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(growth_apply_gateway, "db", lambda: _fake_db(path))
    monkeypatch.setattr(growth_apply_review, "db", lambda: _fake_db(path))


def _allow_reviewer(monkeypatch) -> None:
    monkeypatch.setattr(growth_apply_review, "is_superadmin", lambda uid: True)


def _permissive_pause_policy() -> ApplyPolicy:
    return ApplyPolicy(
        allow_pause_resume=True,
        require_distinct_approver=True,
        kill_switch_enabled=False,
    )


def test_non_superadmin_requires_explicit_review_permission(monkeypatch):
    monkeypatch.setattr(growth_apply_review, "is_superadmin", lambda uid: False)
    monkeypatch.setattr(growth_apply_review, "is_staff", lambda uid: True)
    monkeypatch.setattr(growth_apply_review, "has_explicit_allowed_perm", lambda uid, perm: False)

    assert growth_apply_review.can_review_growth_apply(11) is False

    monkeypatch.setattr(growth_apply_review, "has_explicit_allowed_perm", lambda uid, perm: True)
    assert growth_apply_review.can_review_growth_apply(11) is True


def test_reject_confirmation_is_admin_bound_hashed_and_one_time(tmp_path, monkeypatch):
    path = tmp_path / "review_reject.db"
    _prepare(path)
    _patch_db(monkeypatch, path)
    _allow_reviewer(monkeypatch)

    request = growth_apply_gateway.create_apply_request(
        action_type="campaign_pause",
        target_platform="vk_ads",
        target_ref="campaign:9",
        payload={"risk": "high"},
        requested_by=1,
    )
    prepared = growth_apply_review.prepare_review_confirmation(
        request_id=int(request["id"]),
        decision="reject",
        admin_id=2,
    )

    with _fake_db(path) as conn:
        stored = conn.execute(
            "SELECT token_hash, status FROM growth_apply_confirmations"
        ).fetchone()
    assert stored["token_hash"] != prepared["token"]
    assert len(stored["token_hash"]) == 64
    assert stored["status"] == "pending"

    with pytest.raises(PermissionError, match="admin_mismatch"):
        growth_apply_review.consume_review_confirmation(token=prepared["token"], admin_id=3)

    result = growth_apply_review.consume_review_confirmation(token=prepared["token"], admin_id=2)
    assert result["request"]["status"] == "rejected"
    assert result["request"]["dispatch_allowed"] == 0

    with pytest.raises(ValueError, match="not_pending"):
        growth_apply_review.consume_review_confirmation(token=prepared["token"], admin_id=2)


def test_policy_is_rechecked_after_confirmation_preparation(tmp_path, monkeypatch):
    path = tmp_path / "review_policy.db"
    _prepare(path)
    _patch_db(monkeypatch, path)
    _allow_reviewer(monkeypatch)
    monkeypatch.setattr(growth_apply_review, "current_apply_policy", _permissive_pause_policy)
    monkeypatch.setattr(growth_apply_gateway, "current_apply_policy", _permissive_pause_policy)

    request = growth_apply_gateway.create_apply_request(
        action_type="campaign_pause",
        target_platform="yandex_ads",
        target_ref="campaign:77",
        payload={"risk": "low"},
        requested_by=10,
    )
    prepared = growth_apply_review.prepare_review_confirmation(
        request_id=int(request["id"]),
        decision="approve",
        admin_id=11,
    )

    monkeypatch.setattr(growth_apply_gateway, "current_apply_policy", ApplyPolicy)
    with pytest.raises(ValueError, match="apply_policy_blocked"):
        growth_apply_review.consume_review_confirmation(token=prepared["token"], admin_id=11)

    with _fake_db(path) as conn:
        row = conn.execute(
            "SELECT status, dispatch_allowed FROM growth_apply_requests WHERE id=?",
            (int(request["id"]),),
        ).fetchone()
        token_status = conn.execute(
            "SELECT status FROM growth_apply_confirmations"
        ).fetchone()["status"]
    assert row["status"] == "pending_review"
    assert row["dispatch_allowed"] == 0
    assert token_status == "consumed"


def test_expired_confirmation_is_persisted_and_cannot_be_reused(tmp_path, monkeypatch):
    path = tmp_path / "review_expired.db"
    _prepare(path)
    _patch_db(monkeypatch, path)
    _allow_reviewer(monkeypatch)

    request = growth_apply_gateway.create_apply_request(
        action_type="creative_rotate",
        target_platform="telegram_ads",
        target_ref="creative:1",
        payload={"risk": "medium"},
        requested_by=20,
    )
    prepared = growth_apply_review.prepare_review_confirmation(
        request_id=int(request["id"]),
        decision="reject",
        admin_id=21,
    )
    past = (growth_apply_review._utc_now() - timedelta(seconds=1)).isoformat()
    with _fake_db(path) as conn:
        conn.execute(
            "UPDATE growth_apply_confirmations SET expires_at=?",
            (past,),
        )

    with pytest.raises(ValueError, match="expired"):
        growth_apply_review.consume_review_confirmation(token=prepared["token"], admin_id=21)

    with _fake_db(path) as conn:
        status = conn.execute(
            "SELECT status FROM growth_apply_confirmations"
        ).fetchone()["status"]
    assert status == "expired"

    with pytest.raises(ValueError, match="not_pending"):
        growth_apply_review.consume_review_confirmation(token=prepared["token"], admin_id=21)


def test_confirmation_schema_rejects_unknown_decisions(tmp_path):
    path = tmp_path / "review_schema.db"
    _prepare(path)
    with _fake_db(path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO growth_apply_confirmations(
                    token_hash, request_id, decision, admin_id, status,
                    created_at, expires_at
                ) VALUES('hash', 1, 'execute', 2, 'pending', 'now', 'later')
                """
            )
