from __future__ import annotations

import sqlite3
import uuid

import pytest

from core.time_utils import utc_now_iso
from services.db import db


def test_jobs_job_key_unique_index_is_database_enforced() -> None:
    job_key = f"test:db-job-key-unique:{uuid.uuid4().hex}"
    now = utc_now_iso()
    try:
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE job_key=?", (job_key,))
            conn.execute(
                """
                INSERT INTO jobs(user_id, job_type, run_at_utc, payload, job_key)
                VALUES(?,?,?,?,?)
                """.strip(),
                (-930000001, "test_unique_job_key", now, "{}", job_key),
            )

        with pytest.raises(sqlite3.IntegrityError):
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO jobs(user_id, job_type, run_at_utc, payload, job_key)
                    VALUES(?,?,?,?,?)
                    """.strip(),
                    (-930000002, "test_unique_job_key", now, "{}", job_key),
                )
    finally:
        with db() as conn:
            conn.execute("DELETE FROM jobs WHERE job_key=?", (job_key,))
