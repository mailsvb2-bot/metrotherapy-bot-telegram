from __future__ import annotations

import json
from typing import Any

from services.db import db
from services.growth_apply_gateway import current_apply_policy
from services.growth_apply_gateway_core import evaluate_apply_policy
from services.migrations._helpers import table_exists


class GrowthApplyReadUnavailable(RuntimeError):
    pass


def _rowdict(row: Any | None) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    if hasattr(row, "keys"):
        return {str(key): row[key] for key in row.keys()}
    return {}


def read_apply_request_preview(*, request_id: int) -> dict[str, Any]:
    with db() as conn:
        if not table_exists(conn, "growth_apply_requests"):
            raise GrowthApplyReadUnavailable("growth_apply_requests_schema_not_migrated")
        row = conn.execute(
            "SELECT * FROM growth_apply_requests WHERE id=? LIMIT 1",
            (int(request_id),),
        ).fetchone()
    request = _rowdict(row)
    if not request:
        raise ValueError("growth_apply_request_not_found")

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
        approver_id=None,
        policy=current_apply_policy(),
    )
    return {
        "request": request,
        "evaluation": evaluation,
        "can_approve": False,
        "can_reject": False,
    }
