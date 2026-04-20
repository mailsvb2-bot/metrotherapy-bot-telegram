from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Incident:
    code: str
    where: str
    ts: float
    message: str
    context: Dict[str, Any]
    stack: str


def _incident_path() -> str:
    # Put incidents into observability/ by default (repo-local, no PII required)
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    return os.path.join(base, "observability", "incidents.jsonl")


def log_incident(code: str, where: str, message: str, context: Optional[Dict[str, Any]] = None) -> str:
    inc = Incident(
        code=str(code),
        where=str(where),
        ts=time.time(),
        message=str(message),
        context=dict(context or {}),
        stack="".join(traceback.format_stack(limit=50)),
    )
    path = _incident_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    incident_id = f"{int(inc.ts)}-{abs(hash((inc.code, inc.where, inc.message)))%10**10}"
    rec = {
        "incident_id": incident_id,
        "code": inc.code,
        "where": inc.where,
        "ts": inc.ts,
        "message": inc.message,
        "context": inc.context,
        "stack": inc.stack,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return incident_id
