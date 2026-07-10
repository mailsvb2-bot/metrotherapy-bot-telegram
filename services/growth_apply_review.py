from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from services.admin import is_staff, is_superadmin
from services.admin_permissions import GROWTH_APPLY_REVIEW_PERMISSION, has_explicit_allowed_perm
from services.db import db, tx
from services.growth_apply_gateway import current_apply_policy, decide_apply_request
from services.growth_apply_gateway_core import evaluate_apply_policy
from services.migrations._helpers import table_exists

log = logging.getLogger(__name__)


class GrowthApplyReviewUnavailable(RuntimeError):
    pass


class GrowthApplyReviewDenied(PermissionError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _rowdict(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return {}


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def _ensure_schema(conn: Any) -> None:
    if not table_exists(conn, "growth_apply_requests"):
        raise GrowthApplyReviewUnavailable("growth_apply_requests_schema_not_migrated")
    if not table_exists(conn, "growth_apply_confirmations"):
        raise GrowthApplyReviewUnavailable("growth_apply_confirmations_schema_not_migrated")


def can_review_growth_apply(user_id: int) -> bool:
    uid = int(user_id)
    if is_superadmin(uid):
        return True
    if not is_staff(uid):
        return False
    try:
        return has_explicit_allowed_perm(uid, GROWTH_APPLY_REVIEW_PERMISSION)
    except sqlite3.Error:
        log.exception("Growth apply review permission lookup failed")
        return False
    except OSError:
        log.exception("Growth apply review permission storage failed")
        return False
    except RuntimeError:
        log.exception("Growth apply review permission runtime failed")
        return False


def _require_reviewer(user_id: int) -> int:
    uid = int(user_id)
    if not can_review_growth_apply(uid):
        raise GrowthApplyReviewDenied("growth_apply_review_permission_required")
    return uid


def _load_request(conn: Any, request_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM growth_apply_requests WHERE id=? LIMIT 1",
        (int(request_id),),
    ).fetchone()
    data = _rowdict(row)
    if not data:
        raise ValueError("growth_apply_request_not_found")
    return data


def review_request_preview(*, request_id: int, admin_id: int) -> dict[str, Any]:
    reviewer_id = _require_reviewer(admin_id)
    with db() as conn:
        _ensure_schema(conn)
        request = _load_request(conn, int(request_id))

    try:
        payload = json.loads(str(request.get("payload_json") or "{}"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    evaluation = evaluate_apply_policy(
        action_type=str(request.get("action_type") or ""),
        payload=payload,
        requester_id=int(request.get("requested_by") or 0),
        approver_id=reviewer_id,
        policy=current_apply_policy(),
    )
    status = str(request.get("status") or "")
    return {
        "request": request,
        "evaluation": evaluation,
        "can_approve": status == "pending_review" and bool(evaluation.get("policy_passed")),
        "can_reject": status == "pending_review",
    }


def prepare_review_confirmation(
    *,
    request_id: int,
    decision: str,
    admin_id: int,
    ttl_seconds: int = 120,
) -> dict[str, Any]:
    reviewer_id = _require_reviewer(admin_id)
    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in {"approve", "reject"}:
        raise ValueError("invalid_review_decision")

    preview = review_request_preview(request_id=int(request_id), admin_id=reviewer_id)
    if normalized_decision == "approve" and not preview["can_approve"]:
        violations = ",".join(str(x) for x in preview["evaluation"].get("violations") or [])
        raise ValueError(f"apply_policy_blocked:{violations}")
    if normalized_decision == "reject" and not preview["can_reject"]:
        raise ValueError("apply_request_not_pending")

    raw_token = secrets.token_urlsafe(8)
    token_hash = _token_hash(raw_token)
    now = _utc_now()
    ttl = max(30, min(int(ttl_seconds), 300))
    expires_at = now + timedelta(seconds=ttl)

    with db() as conn:
        _ensure_schema(conn)
        with tx(conn):
            current = _load_request(conn, int(request_id))
            if str(current.get("status") or "") != "pending_review":
                raise ValueError("apply_request_not_pending")
            conn.execute(
                """
                UPDATE growth_apply_confirmations
                SET status='cancelled'
                WHERE request_id=? AND admin_id=? AND status='pending'
                """.strip(),
                (int(request_id), reviewer_id),
            )
            conn.execute(
                """
                INSERT INTO growth_apply_confirmations(
                    token_hash, request_id, decision, admin_id, status,
                    created_at, expires_at
                ) VALUES(?,?,?,?,?,?,?)
                """.strip(),
                (
                    token_hash,
                    int(request_id),
                    normalized_decision,
                    reviewer_id,
                    "pending",
                    now.isoformat(),
                    expires_at.isoformat(),
                ),
            )

    return {
        "token": raw_token,
        "decision": normalized_decision,
        "expires_at": expires_at.isoformat(),
        **preview,
    }


def consume_review_confirmation(*, token: str, admin_id: int) -> dict[str, Any]:
    reviewer_id = _require_reviewer(admin_id)
    token_hash = _token_hash(token)
    now = _utc_now()
    confirmation: dict[str, Any] = {}
    expired = False

    with db() as conn:
        _ensure_schema(conn)
        with tx(conn):
            row = conn.execute(
                "SELECT * FROM growth_apply_confirmations WHERE token_hash=? LIMIT 1",
                (token_hash,),
            ).fetchone()
            confirmation = _rowdict(row)
            if not confirmation:
                raise ValueError("review_confirmation_not_found")
            if int(confirmation.get("admin_id") or 0) != reviewer_id:
                raise GrowthApplyReviewDenied("review_confirmation_admin_mismatch")
            if str(confirmation.get("status") or "") != "pending":
                raise ValueError("review_confirmation_not_pending")

            expires_at = datetime.fromisoformat(str(confirmation.get("expires_at") or ""))
            if expires_at <= now:
                conn.execute(
                    """
                    UPDATE growth_apply_confirmations
                    SET status='expired', consumed_at=?
                    WHERE token_hash=? AND status='pending'
                    """.strip(),
                    (now.isoformat(), token_hash),
                )
                expired = True
            else:
                conn.execute(
                    """
                    UPDATE growth_apply_confirmations
                    SET status='consumed', consumed_at=?
                    WHERE token_hash=? AND status='pending'
                    """.strip(),
                    (now.isoformat(), token_hash),
                )
                changed = conn.execute("SELECT changes() AS n").fetchone()
                if int(_rowdict(changed).get("n") or 0) != 1:
                    raise RuntimeError("review_confirmation_concurrent_consume")

    if expired:
        raise ValueError("review_confirmation_expired")

    decision = str(confirmation.get("decision") or "")
    result = decide_apply_request(
        request_id=int(confirmation.get("request_id") or 0),
        decision=decision,
        decided_by=reviewer_id,
        reason=f"telegram_two_step_{decision}",
    )
    return {
        "decision": decision,
        "request": result,
    }


def cancel_review_confirmation(*, token: str, admin_id: int) -> bool:
    reviewer_id = _require_reviewer(admin_id)
    token_hash = _token_hash(token)
    with db() as conn:
        _ensure_schema(conn)
        with tx(conn):
            conn.execute(
                """
                UPDATE growth_apply_confirmations
                SET status='cancelled', consumed_at=?
                WHERE token_hash=? AND admin_id=? AND status='pending'
                """.strip(),
                (_utc_now().isoformat(), token_hash, reviewer_id),
            )
            changed = conn.execute("SELECT changes() AS n").fetchone()
            return int(_rowdict(changed).get("n") or 0) == 1
