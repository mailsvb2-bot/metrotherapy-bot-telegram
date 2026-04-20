from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from services.idempotency_keys import for_job_run_at
from services.migrations._helpers import migration_applied, mark_migration

NAME = "scheduled_jobs_to_jobs_v1"


def apply(conn: sqlite3.Connection) -> None:
    """Migrate legacy scheduled_jobs (unix timebase) -> jobs (UTC ISO).

    Safety:
    - runs once via schema_migrations marker
    - INSERT OR IGNORE by UNIQUE(job_key)
    - deletes legacy rows to avoid duplicates if some old worker is still around
    """
    log = logging.getLogger(__name__)
    try:
        if migration_applied(conn, NAME):
            return
    except sqlite3.Error:
        # If schema_migrations can't be inspected - do nothing; schema will create it later.
        return

    try:
        legacy_rows = conn.execute(
            """
            SELECT job_id, user_id, kind, run_at, payload
            FROM scheduled_jobs
            WHERE status IN ('pending','running') AND kind='post_prompt'
            """.strip()
        ).fetchall()
    except sqlite3.Error:
        # no table -> nothing to migrate
        mark_migration(conn, NAME)
        return

    moved = 0
    for job_id, user_id, kind, run_at, payload_json in legacy_rows:
        try:
            run_at_i = int(run_at)
        except (TypeError, ValueError):
            continue

        run_at_iso = datetime.fromtimestamp(run_at_i, tz=timezone.utc).replace(microsecond=0).isoformat()

        try:
            payload = json.loads(payload_json or "{}")
            if not isinstance(payload, dict):
                payload = {}
        except (json.JSONDecodeError, TypeError):
            payload = {}

        payload.setdefault("legacy_job_id", str(job_id))
        payload.setdefault("run_at", run_at_i)
        p = json.dumps(payload, ensure_ascii=False)

        ref = str(payload.get("session_id") or payload.get("legacy_job_id") or job_id)
        job_key = f"{for_job_run_at('post_prompt', ref, int(run_at_i))}:a0"

        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO jobs(
                    user_id, job_type, run_at_utc, payload,
                    job_key, retries, locked_at, lock_token, done_at, last_error
                ) VALUES(?,?,?,?,?, 0, NULL, NULL, NULL, NULL)
                """.strip(),
                (int(user_id), "post_prompt", run_at_iso, p, job_key),
            )
            conn.execute("DELETE FROM scheduled_jobs WHERE job_id=?", (str(job_id),))
            moved += 1
        except sqlite3.Error:
            log.exception("scheduled_jobs -> jobs: failed to migrate job_id=%s", job_id)

    mark_migration(conn, NAME)
    log.info("scheduled_jobs -> jobs migration applied: moved=%s", moved)
