from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

GATEWAY_MODE = "approval_only"
DISPATCH_ALLOWED = False

_ALLOWED_ACTION_TYPES = frozenset({
    "budget_change",
    "campaign_pause",
    "campaign_resume",
    "creative_rotate",
})

_ALLOWED_STATUSES = frozenset({
    "pending_review",
    "approved",
    "rejected",
    "expired",
})

_RISK_ORDER = {"low": 1, "medium": 2, "high": 3, "critical": 4}


@dataclass(frozen=True)
class ApplyPolicy:
    max_abs_budget_delta_minor: int = 0
    max_budget_delta_pct: float = 0.0
    allow_pause_resume: bool = False
    allow_creative_rotate: bool = False
    require_distinct_approver: bool = True
    kill_switch_enabled: bool = True


def _clean(value: Any, *, limit: int = 160) -> str:
    text = " ".join(str(value or "").replace("\n", " ").replace("\r", " ").split())
    return text[:limit]


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def stable_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def normalize_action_type(value: Any) -> str:
    action_type = _clean(value, limit=64).lower().replace("-", "_").replace(" ", "_")
    if action_type not in _ALLOWED_ACTION_TYPES:
        raise ValueError(f"unsupported_apply_action:{action_type or 'empty'}")
    return action_type


def normalize_status(value: Any) -> str:
    status = _clean(value, limit=64).lower()
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"unsupported_apply_status:{status or 'empty'}")
    return status


def normalize_payload(value: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    out: dict[str, Any] = {}
    for key, raw in source.items():
        clean_key = _clean(key, limit=64)
        if not clean_key:
            continue
        if isinstance(raw, bool):
            out[clean_key] = raw
        elif isinstance(raw, int):
            out[clean_key] = raw
        elif isinstance(raw, float):
            out[clean_key] = round(raw, 6)
        elif raw is not None:
            cleaned = _clean(raw, limit=320)
            if cleaned:
                out[clean_key] = cleaned
        if len(out) >= 32:
            break
    return out


def action_request_key(
    *,
    action_type: str,
    target_platform: Any,
    target_ref: Any,
    payload: dict[str, Any] | None,
) -> str:
    normalized_type = normalize_action_type(action_type)
    seed = {
        "action_type": normalized_type,
        "target_platform": _clean(target_platform, limit=64).lower(),
        "target_ref": _clean(target_ref, limit=192),
        "payload": normalize_payload(payload),
    }
    digest = hashlib.sha256(stable_json(seed).encode("utf-8")).hexdigest()[:32]
    return f"growth_apply:v1:{normalized_type}:{digest}"


def evaluate_apply_policy(
    *,
    action_type: str,
    payload: dict[str, Any] | None,
    requester_id: int,
    approver_id: int | None,
    policy: ApplyPolicy,
) -> dict[str, Any]:
    normalized_type = normalize_action_type(action_type)
    normalized_payload = normalize_payload(payload)
    violations: list[str] = []

    if policy.kill_switch_enabled:
        violations.append("global_kill_switch_enabled")

    if policy.require_distinct_approver and approver_id is not None and int(approver_id) == int(requester_id):
        violations.append("requester_cannot_self_approve")

    if normalized_type == "budget_change":
        delta_minor = abs(safe_int(normalized_payload.get("delta_minor")))
        delta_pct = abs(safe_float(normalized_payload.get("delta_pct")))
        if policy.max_abs_budget_delta_minor <= 0:
            violations.append("budget_changes_disabled")
        elif delta_minor > int(policy.max_abs_budget_delta_minor):
            violations.append("budget_delta_minor_exceeds_limit")
        if policy.max_budget_delta_pct <= 0:
            violations.append("budget_percent_changes_disabled")
        elif delta_pct > float(policy.max_budget_delta_pct):
            violations.append("budget_delta_pct_exceeds_limit")
    elif normalized_type in {"campaign_pause", "campaign_resume"} and not policy.allow_pause_resume:
        violations.append("pause_resume_disabled")
    elif normalized_type == "creative_rotate" and not policy.allow_creative_rotate:
        violations.append("creative_rotate_disabled")

    risk = _clean(normalized_payload.get("risk"), limit=16).lower() or "high"
    if risk not in _RISK_ORDER:
        risk = "high"
    if _RISK_ORDER[risk] >= _RISK_ORDER["critical"]:
        violations.append("critical_risk_not_approvable")

    return {
        "action_type": normalized_type,
        "mode": GATEWAY_MODE,
        "dispatch_allowed": DISPATCH_ALLOWED,
        "policy_passed": not violations,
        "violations": violations,
        "risk": risk,
        "payload": normalized_payload,
    }


def next_status(*, current_status: str, decision: str, policy_passed: bool) -> str:
    current = normalize_status(current_status)
    normalized_decision = _clean(decision, limit=32).lower()
    if current != "pending_review":
        raise ValueError(f"apply_request_not_pending:{current}")
    if normalized_decision == "reject":
        return "rejected"
    if normalized_decision != "approve":
        raise ValueError(f"unsupported_apply_decision:{normalized_decision or 'empty'}")
    if not policy_passed:
        raise ValueError("apply_policy_blocked")
    return "approved"
