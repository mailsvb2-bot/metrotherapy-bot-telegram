from __future__ import annotations
import sqlite3

from typing import Protocol, Any, Dict

from core.ai.decision_types import Decision
from core.runtime.sovereignty.enforcement import require_token, set_current_token
from core.runtime.sovereignty.safe_mode import SAFE_MODE
from services.events import log_runtime_event


class ActionRunner(Protocol):
    async def run(self, payload: Dict[str, Any]) -> Any: ...


async def execute(decision: Decision, runner: ActionRunner) -> Any:
    require_token(decision.token)
    if SAFE_MODE.active:
        # always degrade; runner may still render safe content
        return await runner.run({"type": "safe_content", "reason": SAFE_MODE.reason, "_mode": "safe"})
    set_current_token(decision.token)
    try:
        payload = dict(decision.payload)
        payload.setdefault("_decision_id", decision.decision_id)
        payload.setdefault("_correlation_id", decision.token.nonce)
        payload.setdefault("_policy", decision.meta.get("policy"))
        try:
            res = await runner.run(payload)
            uid = int(payload.get('user_id') or payload.get('chat_id') or 0)
            if uid:
                try:
                    log_runtime_event(uid, event_type='action_executed', payload={'type': payload.get('type')}, correlation_id=payload.get('_correlation_id'), decision_id=payload.get('_decision_id'), source='runtime')
                except (sqlite3.Error, TypeError, ValueError):
                    pass
            return res
        except Exception as e:  # validator: allow-wide-except
            uid = int(payload.get('user_id') or payload.get('chat_id') or 0)
            if uid:
                try:
                    log_runtime_event(uid, event_type='action_failed', payload={'type': payload.get('type'), 'err': str(e)[:200]}, correlation_id=payload.get('_correlation_id'), decision_id=payload.get('_decision_id'), source='runtime')
                except (sqlite3.Error, TypeError, ValueError):
                    pass
            raise
    finally:
        set_current_token(None)
