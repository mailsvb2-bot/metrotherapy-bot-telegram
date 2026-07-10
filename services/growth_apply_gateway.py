from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from services.db import db, tx
from services.growth_apply_gateway_core import (
    ApplyPolicy,
    GATEWAY_MODE,
    action_request_key,
    evaluate_apply_policy,
    next_status,
    normalize_action_type,
    normalize_payload,
    stable_json,
)
from services.migrations._helpers import table_exists

log = logging.getLogger(__name__)


class GrowthApplyGatewayUnavailable(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def current_apply_policy() -> ApplyPolicy:
    return ApplyPolicy(
        max_abs_budget_delta_minor=max(0, int(os.getenv("GROWTH_APPLY_MAX_BUDGET_DELTA_MINOR", "0") or "0")),
        max_budget_delta_pct=max(0.0, float(os.getenv("GROWTH_APPLY_MAX_BUDGET_DELTA_PCT", "0") or "0")),
        allow_pause_resume=_env_bool("GROWTH_APPLY_ALLOW_PAUSE_RESUME", False),
        allow_creative_rotate=_env_bool("GROWTH_APPLY_ALLOW_CREATIVE_ROTATE", False),
        require_distinct_approver=_env_bool("GROWTH_APPLY_REQUIRE_DISTINCT_APPROVER", True),
        kill_switch_enabled=_env_bool("GROWTH_APPLY_KILL_SWITCH", True),
    )


def ensure_schema(conn: Any) -> None:
    if not table_exists(conn, "growth_apply_requests"):
        raise GrowthApplyGatewayUnavailable("growth_apply_requests_schema_not_migrated")
    if not table_exists(conn, "growth_apply_audit"):
        raise GrowthApplyGatewayUnavailable("growth_apply_audit_schema_not_migrated")


def _rowdict(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return {}


def _audit(
    conn: Any,
    *,
    request_id: int,
    event_type: str,
    actor_id: int,
    before_status: str | None,
    after_status: str | None,
    details: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO growth_apply_audit(
            request_id, event_type, actor_id, before_status, after_status,
            details_json, created_at
        ) VALUES(?,?,?,?,?,?,?)
        """.strip(),
        (
            int(request_id),
            str(event_type),
            int(actor_id),
            before_status,
            after_status,
            stable_json(normalize_payload(details)),
            _utc_now().isoformat(),
        ),
    )


def create_apply_request(
    *,
    action_type: str,
    target_platform: str,
    target_ref: str,
    payload: dict[str, Any] | None,
    requested_by: int,
    ttl_minutes: int = 60,
) -> dict[str, Any]:
    normalized_type = normalize_action_type(action_type)
    normalized_payload = normalize_payload(payload)
    policy = current_apply_policy()
    evaluation = evaluate_apply_policy(
        action_type=normalized_type,
        payload=normalized_payload,
        requester_id=int(requested_by),
        approver_id=None,
        policy=policy,
    )
    request_key = action_request_key(
        action_type=normalized_type,
        target_platform=target_platform,
        target_ref=target_ref,
        payload=normalized_payload,
    )
    now = _utc_now()
    expires_at = now + timedelta(minutes=max(5, min(int(ttl_minutes), 24 * 60)))

    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            conn.execute(
                """
                INSERT OR IGNORE INTO growth_apply_requests(
                    request_key, action_type, target_platform, target_ref,
                    payload_json, policy_json, status, mode, dispatch_allowed,
                    requested_by, requested_at, expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """.strip(),
                (
                    request_key,
                    normalized_type,
                    str(target_platform or "none").strip().lower() or "none",
                    str(target_ref or "").strip(),
                    stable_json(normalized_payload),
                    stable_json({**asdict(policy), "evaluation": evaluation}),
                    "pending_review",
                    GATEWAY_MODE,
                    0,
                    int(requested_by),
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM growth_apply_requests WHERE request_key=? LIMIT 1",
                (request_key,),
            ).fetchone()
            data = _rowdict(row)
            if not data:
                raise RuntimeError("growth_apply_request_insert_failed")
            request_id = int(data["id"])
            audit_count = conn.execute(
                "SELECT COUNT(*) AS n FROM growth_apply_audit WHERE request_id=?",
                (request_id,),
            ).fetchone()
            n = int(_rowdict(audit_count).get("n") or 0)
            if n == 0:
                _audit(
                    conn,
                    request_id=request_id,
                    event_type="request_created",
                    actor_id=int(requested_by),
                    before_status=None,
                    after_status="pending_review",
                    details={"policy_passed": evaluation["policy_passed"], "violations": ",".join(evaluation["violations"])},
                )
    return data


def decide_apply_request(
    *,
    request_id: int,
    decision: str,
    decided_by: int,
    reason: str,
) -> dict[str, Any]:
    now = _utc_now()
    with db() as conn:
        ensure_schema(conn)
        with tx(conn):
            row = conn.execute(
                "SELECT * FROM growth_apply_requests WHERE id=? LIMIT 1",
                (int(request_id),),
            ).fetchone()
            data = _rowdict(row)
            if not data:
                raise ValueError("growth_apply_request_not_found")

            current_status = str(data.get("status") or "")
            expires_at_raw = str(data.get("expires_at") or "")
            if expires_at_raw and datetime.fromisoformat(expires_at_raw) <= now:
                conn.execute(
                    "UPDATE growth_apply_requests SET status='expired', decided_at=? WHERE id=? AND status='pending_review'",
                    (now.isoformat(), int(request_id)),
                )
                _audit(
                    conn,
                    request_id=int(request_id),
                    event_type="request_expired",
                    actor_id=int(decided_by),
                    before_status=current_status,
                    after_status="expired",
                    details={"reason": "ttl_expired"},
                )
                raise ValueError("growth_apply_request_expired")

            payload = json.loads(str(data.get("payload_json") or "{}"))
            policy = current_apply_policy()
            evaluation = evaluate_apply_policy(
                action_type=str(data.get("action_type") or ""),
                payload=payload if isinstance(payload, dict) else {},
                requester_id=int(data.get("requested_by") or 0),
                approver_id=int(decided_by),
                policy=policy,
            )
            target_status = next_status(
                current_status=current_status,
                decision=decision,
                policy_passed=bool(evaluation["policy_passed"]),
            )
            conn.execute(
                """
                UPDATE growth_apply_requests
                SET status=?, decided_by=?, decided_at=?, decision_reason=?,
                    policy_json=?, dispatch_allowed=0, mode=?
                WHERE id=? AND status='pending_review'
                """.strip(),
                (
                    target_status,
                    int(decided_by),
                    now.isoformat(),
                    str(reason or "").strip()[:500],
                    stable_json({**asdict(policy), "evaluation": evaluation}),
                    GATEWAY_MODE,
                    int(request_id),
                ),
            )
            changed = conn.execute("SELECT changes() AS n").fetchone()
            if int(_rowdict(changed).get("n") or 0) != 1:
                raise RuntimeError("growth_apply_request_concurrent_transition")
            _audit(
                conn,
                request_id=int(request_id),
                event_type=f"request_{target_status}",
                actor_id=int(decided_by),
                before_status=current_status,
                after_status=target_status,
                details={"reason": reason, "policy_passed": evaluation["policy_passed"]},
            )
            updated = conn.execute(
                "SELECT * FROM growth_apply_requests WHERE id=? LIMIT 1",
                (int(request_id),),
            ).fetchone()
    return _rowdict(updated)


def apply_gateway_snapshot(*, limit: int = 20) -> dict[str, Any]:
    policy = current_apply_policy()
    with db() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            """
            SELECT id, action_type, target_platform, target_ref, status, mode,
                   dispatch_allowed, requested_by, requested_at, decided_by,
                   decided_at, decision_reason, expires_at
            FROM growth_apply_requests
            ORDER BY id DESC
            LIMIT ?
            """.strip(),
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        counts = conn.execute(
            "SELECT status, COUNT(*) AS n FROM growth_apply_requests GROUP BY status"
        ).fetchall()
    return {
        "ok": True,
        "mode": GATEWAY_MODE,
        "dispatch_allowed": False,
        "kill_switch_enabled": policy.kill_switch_enabled,
        "policy": asdict(policy),
        "counts": {str(_rowdict(row).get("status")): int(_rowdict(row).get("n") or 0) for row in counts},
        "latest": [_rowdict(row) for row in rows],
    }


def build_apply_gateway_report() -> str:
    try:
        snapshot = apply_gateway_snapshot()
    except GrowthApplyGatewayUnavailable as exc:
        return "\n".join([
            "🛡 Guarded Apply Gateway",
            "Статус: DEGRADED",
            f"Причина: {exc}",
            "dispatch_allowed=False",
        ])
    counts = snapshot["counts"]
    lines = [
        "🛡 Guarded Apply Gateway",
        "",
        f"mode={snapshot['mode']}",
        f"dispatch_allowed={snapshot['dispatch_allowed']}",
        f"kill_switch_enabled={snapshot['kill_switch_enabled']}",
        "",
        f"pending: {int(counts.get('pending_review') or 0)}",
        f"approved: {int(counts.get('approved') or 0)}",
        f"rejected: {int(counts.get('rejected') or 0)}",
        f"expired: {int(counts.get('expired') or 0)}",
        "",
        "Approved означает только прохождение review. Исполняющего adapter в этом контуре нет.",
    ]
    for item in snapshot["latest"][:8]:
        lines.append(
            f"#{item.get('id')} {item.get('action_type')} / {item.get('target_platform')} / {item.get('status')}"
        )
    return "\n".join(lines)
