from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

from core.time_utils import utc_now_iso
from services.db import db

SYNTHETIC_USER_ID_MIN = -999_999_999
SYNTHETIC_USER_ID_MAX = -900_000_000


@dataclass(frozen=True)
class ProbeRun:
    id: int
    probe_type: str
    run_id: str
    user_id: int | None
    started_at_utc: str
    finished_at_utc: str | None
    status: str
    cleanup_status: str
    rows_touched: int
    error: str | None
    evidence: dict[str, Any]


def assert_synthetic_user_id(user_id: int) -> None:
    value = int(user_id)
    if not (SYNTHETIC_USER_ID_MIN <= value <= SYNTHETIC_USER_ID_MAX):
        raise ValueError(
            "probe synthetic user_id must be in reserved namespace "
            f"[{SYNTHETIC_USER_ID_MIN}, {SYNTHETIC_USER_ID_MAX}], got {value}"
        )


def _json_dumps(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _row_value(row, key: str, index: int):
    if row is None:
        return None
    if hasattr(row, "keys"):
        try:
            return row[key]
        except (KeyError, TypeError, IndexError):
            pass
    try:
        return row[index]
    except (TypeError, KeyError, IndexError):
        return None


def start_probe_run(*, probe_type: str, user_id: int | None = None, run_id: str | None = None, evidence: dict[str, Any] | None = None) -> str:
    if user_id is not None:
        assert_synthetic_user_id(int(user_id))
    probe_run_id = (run_id or uuid.uuid4().hex).strip()
    if not probe_run_id:
        raise ValueError("probe run_id must not be empty")
    now = utc_now_iso()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO probe_runs(
                probe_type, run_id, user_id, started_at_utc, status,
                cleanup_status, rows_touched, evidence_json
            ) VALUES(?,?,?,?,?,?,?,?)
            """.strip(),
            (
                str(probe_type),
                probe_run_id,
                int(user_id) if user_id is not None else None,
                now,
                "running",
                "pending",
                0,
                _json_dumps(evidence),
            ),
        )
    return probe_run_id


def finish_probe_run(
    *,
    run_id: str,
    status: str,
    cleanup_status: str,
    rows_touched: int = 0,
    error: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> None:
    now = utc_now_iso()
    with db() as conn:
        conn.execute(
            """
            UPDATE probe_runs
            SET finished_at_utc=?, status=?, cleanup_status=?, rows_touched=?, error=?, evidence_json=?
            WHERE run_id=?
            """.strip(),
            (
                now,
                str(status),
                str(cleanup_status),
                int(rows_touched),
                str(error)[:1000] if error else None,
                _json_dumps(evidence),
                str(run_id),
            ),
        )


def get_recent_probe_runs(*, limit: int = 10) -> list[ProbeRun]:
    safe_limit = max(1, min(int(limit), 50))
    with db() as conn:
        rows = conn.execute(
            """
            SELECT id, probe_type, run_id, user_id, started_at_utc, finished_at_utc,
                   status, cleanup_status, rows_touched, error, evidence_json
            FROM probe_runs
            ORDER BY id DESC
            LIMIT ?
            """.strip(),
            (safe_limit,),
        ).fetchall()
    result: list[ProbeRun] = []
    for row in rows:
        result.append(
            ProbeRun(
                id=int(_row_value(row, "id", 0) or 0),
                probe_type=str(_row_value(row, "probe_type", 1) or ""),
                run_id=str(_row_value(row, "run_id", 2) or ""),
                user_id=(int(_row_value(row, "user_id", 3)) if _row_value(row, "user_id", 3) is not None else None),
                started_at_utc=str(_row_value(row, "started_at_utc", 4) or ""),
                finished_at_utc=(str(_row_value(row, "finished_at_utc", 5)) if _row_value(row, "finished_at_utc", 5) else None),
                status=str(_row_value(row, "status", 6) or "unknown"),
                cleanup_status=str(_row_value(row, "cleanup_status", 7) or "unknown"),
                rows_touched=int(_row_value(row, "rows_touched", 8) or 0),
                error=(str(_row_value(row, "error", 9)) if _row_value(row, "error", 9) else None),
                evidence=_json_loads(_row_value(row, "evidence_json", 10)),
            )
        )
    return result


def format_probe_runs_for_admin(*, limit: int = 5) -> str:
    runs = get_recent_probe_runs(limit=limit)
    if not runs:
        return "🧪 Системные проверки\n\nПока нет записей probe ledger."
    lines = ["🧪 Системные проверки", "", "Последние probe-запуски:"]
    for run in runs:
        marker = "✅" if run.status == "ok" and run.cleanup_status == "clean" else "⚠️"
        lines.append(
            f"{marker} #{run.id} {run.probe_type} — {run.status}/{run.cleanup_status}\n"
            f"   run_id={run.run_id[:12]} user={run.user_id} rows={run.rows_touched}\n"
            f"   started={run.started_at_utc} finished={run.finished_at_utc or '-'}"
        )
        if run.error:
            lines.append(f"   error={run.error[:180]}")
    return "\n".join(lines)


__all__ = [
    "ProbeRun",
    "SYNTHETIC_USER_ID_MIN",
    "SYNTHETIC_USER_ID_MAX",
    "assert_synthetic_user_id",
    "start_probe_run",
    "finish_probe_run",
    "get_recent_probe_runs",
    "format_probe_runs_for_admin",
]
