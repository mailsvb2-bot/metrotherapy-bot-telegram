from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.time_utils import utc_now, utc_now_iso
from services.db import db, tx
from services.db.runtime import CONFIG
from services.messenger import delivery_outbox, delivery_pool
from services.probe_safety import (
    ProbeMutationAuthorizationRequired,
    mutation_authorized,
    require_live_db_mutation,
)
from services.schema import init_db

PROBE_PLATFORM = "__probe_postgres_messenger_outbox__"
PROBE_USER_A = -910_000_401
PROBE_USER_B = -910_000_402
PROBE_WORKERS = 4


def _fail(code: str) -> None:
    raise SystemExit(f"POSTGRES_MESSENGER_OUTBOX_FAILED: {code}")


def _cleanup(*, key_prefix: str) -> int:
    with db() as conn:
        cursor = conn.execute(
            "DELETE FROM messenger_delivery_outbox WHERE platform=? AND event_key LIKE ?",
            (PROBE_PLATFORM, f"{key_prefix}%"),
        )
    return max(0, int(getattr(cursor, "rowcount", 0) or 0))


def _insert_probe_row(*, event_key: str, user_id: int) -> None:
    now = utc_now_iso()
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO messenger_delivery_outbox(
                platform,external_user_id,canonical_user_id,event_key,action,replies_json,
                status,attempts,available_at,locked_at,lock_token,last_error,
                created_at,updated_at,sent_at
            ) VALUES(?,?,?,?,?,'[]','pending',0,?,NULL,NULL,'',?,?,NULL)
            """.strip(),
            (
                PROBE_PLATFORM,
                f"probe-user-{int(user_id)}",
                int(user_id),
                str(event_key),
                "postgres_messenger_outbox_probe",
                now,
                now,
                now,
            ),
        )
    if int(getattr(cursor, "rowcount", 0) or 0) != 1:
        _fail("probe_insert_failed")


def _claim_probe_stream_head(*, barrier: threading.Barrier | None = None, lock_ttl_sec: int = 900):
    """Run the exact production Postgres stream-head SQL in an isolated namespace."""

    if barrier is not None:
        barrier.wait(timeout=10)
    now = utc_now().replace(microsecond=0)
    now_iso = now.isoformat()
    stale_before = (now - timedelta(seconds=max(1, int(lock_ttl_sec)))).isoformat()
    token = uuid.uuid4().hex
    with db() as conn:
        with tx(conn):
            row = conn.execute(
                delivery_pool._POSTGRES_STREAM_HEAD_SQL,  # noqa: SLF001 - probe intentionally exercises production SQL
                (PROBE_PLATFORM, now_iso, stale_before, now_iso, token, now_iso),
            ).fetchone()
    if row is None:
        return None
    return delivery_pool._claimed_from_row(row, token)  # noqa: SLF001 - same production row decoder


def run_probe(*, allow_live_db_mutation: bool) -> dict[str, object]:
    require_live_db_mutation(bool(allow_live_db_mutation))
    if not CONFIG.uses_postgres:
        _fail("METRO_DB_ENGINE=postgres is required")
    if not (os.getenv("DATABASE_URL") or "").strip():
        _fail("DATABASE_URL is required")

    init_db()
    prefix = f"postgres-outbox-probe-{uuid.uuid4().hex}-"
    event_keys = [f"{prefix}a1", f"{prefix}a2", f"{prefix}b1"]
    rows_touched = 0
    try:
        rows_touched += _cleanup(key_prefix=prefix)
        _insert_probe_row(event_key=event_keys[0], user_id=PROBE_USER_A)
        _insert_probe_row(event_key=event_keys[1], user_id=PROBE_USER_A)
        _insert_probe_row(event_key=event_keys[2], user_id=PROBE_USER_B)
        rows_touched += 3

        barrier = threading.Barrier(PROBE_WORKERS)
        with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as executor:
            results = list(
                executor.map(
                    lambda _: _claim_probe_stream_head(barrier=barrier, lock_ttl_sec=900),
                    range(PROBE_WORKERS),
                )
            )

        claimed = [item for item in results if item is not None]
        if len(claimed) != 2:
            _fail(f"expected_two_stream_heads_got_{len(claimed)}")
        if {item.canonical_user_id for item in claimed} != {PROBE_USER_A, PROBE_USER_B}:
            _fail("unexpected_probe_user_claimed")
        claimed_keys = {item.event_key for item in claimed}
        if event_keys[0] not in claimed_keys:
            _fail("first_user_a_head_not_claimed")
        if event_keys[1] in claimed_keys:
            _fail("second_user_a_row_claimed_out_of_order")
        if any(item.platform != PROBE_PLATFORM for item in claimed):
            _fail("claim_escaped_probe_platform")

        for item in claimed:
            delivery_outbox.mark_delivery_sent(item)

        second_a = _claim_probe_stream_head(lock_ttl_sec=900)
        if second_a is None:
            _fail("ordered_followup_missing")
        if second_a.event_key != event_keys[1] or second_a.canonical_user_id != PROBE_USER_A:
            _fail("ordered_followup_mismatch")
        delivery_outbox.mark_delivery_sent(second_a)

        with db() as conn:
            rows = conn.execute(
                """
                SELECT event_key,status,attempts,sent_at
                FROM messenger_delivery_outbox
                WHERE platform=? AND event_key IN (?,?,?)
                ORDER BY event_key
                """.strip(),
                (PROBE_PLATFORM, *event_keys),
            ).fetchall()
        if len(rows) != 3:
            _fail("probe_rows_missing")
        if not all(str(row["status"]) == "sent" for row in rows):
            _fail("probe_rows_not_sent")
        if not all(int(row["attempts"] or 0) == 0 for row in rows):
            _fail("probe_attempts_changed")
        if not all(str(row["sent_at"] or "") for row in rows):
            _fail("probe_sent_at_missing")

        payload: dict[str, object] = {
            "ok": True,
            "probe": "postgres_messenger_delivery_pool",
            "isolated_platform": True,
            "parallel_stream_heads": len(claimed),
            "ordered_followup": True,
            "rows_touched": rows_touched,
        }
        return payload
    finally:
        _cleanup(key_prefix=prefix)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe native Postgres messenger outbox concurrency without touching live platform queues"
    )
    parser.add_argument("--allow-live-db-mutation", action="store_true")
    args = parser.parse_args()
    try:
        payload = run_probe(
            allow_live_db_mutation=mutation_authorized(bool(args.allow_live_db_mutation))
        )
    except ProbeMutationAuthorizationRequired as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "applied": False,
                    "database_touched": False,
                    "error_code": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2

    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    print("POSTGRES_MESSENGER_OUTBOX_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
