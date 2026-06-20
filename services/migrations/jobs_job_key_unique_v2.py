from __future__ import annotations

import logging

from services.migrations._helpers import is_migration_applied, mark_migration

log = logging.getLogger(__name__)

NAME = "jobs_job_key_unique_v2"


def _duplicate_job_keys(conn) -> list[str]:
    rows = conn.execute(
        """
        SELECT job_key
        FROM jobs
        WHERE job_key IS NOT NULL
        GROUP BY job_key
        HAVING COUNT(*) > 1
        LIMIT 10
        """.strip()
    ).fetchall()
    duplicates: list[str] = []
    for row in rows:
        if hasattr(row, "keys"):
            duplicates.append(str(row["job_key"]))
        else:
            duplicates.append(str(row[0]))
    return duplicates


def apply(conn) -> None:
    if is_migration_applied(conn, NAME):
        return

    duplicates = _duplicate_job_keys(conn)
    if duplicates:
        raise RuntimeError(
            "Cannot enforce jobs.job_key uniqueness; duplicate job_key values exist: "
            + ", ".join(duplicates)
        )

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_job_key
        ON jobs(job_key)
        WHERE job_key IS NOT NULL
        """.strip()
    )
    mark_migration(conn, NAME)
    log.info("Migration applied: %s", NAME)
