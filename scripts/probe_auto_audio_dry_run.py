from __future__ import annotations

"""Dry-run probe for the auto-audio pre-score path.

The probe avoids Telegram/network sends. It verifies the production-critical local
path that precedes a scheduled auto-audio prompt and records a probe ledger row.
"""

import argparse
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.audio_anchor import pick_for_slot
from services.db import db, mark_delivery_once, was_delivered
from services.idempotency_keys import for_pre_score
from services.mood import create_session, get_session
from services.probe_ledger import assert_synthetic_user_id, finish_probe_run, start_probe_run
from services.schema import init_db
from services.subscription import grant, has_access

DEFAULT_PROBE_USER_ID = -910_000_201
DEFAULT_SLOT = "morning"
PROBE_SOURCE = "auto_audio_dry_run_probe"


@dataclass(frozen=True)
class AutoAudioProbeResult:
    user_id: int
    slot: str
    session_id: int
    run_id: str
    cleanup_status: str
    rows_touched: int


def _kind_for_slot(slot: str) -> str:
    return "work" if slot == "morning" else "home"


def _cleanup_probe_rows(*, user_id: int) -> int:
    touched = 0
    with db() as conn:
        for sql, params in (
            ("DELETE FROM mood_sessions WHERE user_id=? AND source=?", (int(user_id), PROBE_SOURCE)),
            ("DELETE FROM subscriptions WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM idempotency WHERE user_id=?", (int(user_id),)),
            ("DELETE FROM users WHERE user_id=?", (int(user_id),)),
        ):
            cur = conn.execute(sql, params)
            touched += max(int(getattr(cur, "rowcount", 0) or 0), 0)
    return touched


def _ensure_probe_user(*, user_id: int) -> None:
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id, work_time, home_time) VALUES(?,?,?)",
            (int(user_id), "08:30", "19:30"),
        )
        conn.execute(
            "UPDATE users SET work_time=?, home_time=? WHERE user_id=?",
            ("08:30", "19:30", int(user_id)),
        )


def _record_failure(*, run_id: str, rows_touched: int, keep_artifacts: bool, error: BaseException, slot: str) -> None:
    finish_probe_run(
        run_id=run_id,
        status="failed",
        cleanup_status="failed" if keep_artifacts else "unknown",
        rows_touched=rows_touched,
        error=str(error),
        evidence={"slot": slot},
    )


def run_probe(
    *,
    user_id: int = DEFAULT_PROBE_USER_ID,
    slot: str = DEFAULT_SLOT,
    keep_artifacts: bool = False,
    initialize_schema: bool = True,
) -> AutoAudioProbeResult:
    assert_synthetic_user_id(int(user_id))
    if initialize_schema:
        init_db()
    slot = (slot or DEFAULT_SLOT).strip().lower()
    if slot not in {"morning", "evening"}:
        raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED slot must be morning or evening")

    run_id = uuid.uuid4().hex
    start_probe_run(probe_type=PROBE_SOURCE, user_id=int(user_id), run_id=run_id, evidence={"slot": slot})
    rows_touched = 0

    try:
        rows_touched += _cleanup_probe_rows(user_id=int(user_id))

        _ensure_probe_user(user_id=int(user_id))
        rows_touched += 1

        grant(int(user_id), "both", 1, source=PROBE_SOURCE)
        rows_touched += 1
        if not has_access(int(user_id), slot):
            raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED synthetic subscription did not grant access")

        audio = pick_for_slot(slot, 0)
        if audio is None:
            raise SystemExit(f"AUTO_AUDIO_DRY_RUN_FAILED no anchored audio found for slot={slot}")

        local_day = datetime.now(timezone.utc).date().isoformat()
        scheduled_at = for_pre_score(int(user_id), local_day, slot)
        kind = _kind_for_slot(slot)

        if not mark_delivery_once(int(user_id), kind, "pre_score", scheduled_at):
            raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED pre-score idempotency returned duplicate")
        rows_touched += 1
        if not was_delivered(int(user_id), kind, "pre_score", scheduled_at):
            raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED pre-score idempotency row not visible")

        session_id = create_session(
            int(user_id),
            kind=kind,
            source=PROBE_SOURCE,
            day=local_day,
            slot=slot,
            scheduled_at=scheduled_at,
            anchor_id=int(audio.anchor),
        )
        rows_touched += 1
        session = get_session(int(session_id))
        if session is None:
            raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED mood session not found after create_session")
        if int(session.user_id) != int(user_id):
            raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED session user_id mismatch")
        if session.slot != slot:
            raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED session slot mismatch")
        if int(session.anchor_id or 0) != int(audio.anchor):
            raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED session anchor mismatch")

        cleanup_status = "kept"
        if not keep_artifacts:
            rows_touched += _cleanup_probe_rows(user_id=int(user_id))
            cleanup_status = "clean"

        finish_probe_run(
            run_id=run_id,
            status="ok",
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
            evidence={"slot": slot, "session_id": int(session_id), "anchor_id": int(audio.anchor)},
        )
        return AutoAudioProbeResult(
            user_id=int(user_id),
            slot=slot,
            session_id=int(session_id),
            run_id=run_id,
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
        )
    except SystemExit as exc:
        _record_failure(run_id=run_id, rows_touched=rows_touched, keep_artifacts=keep_artifacts, error=exc, slot=slot)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run probe for auto-audio pre-score local path")
    parser.add_argument("--user-id", type=int, default=int(os.getenv("AUTO_AUDIO_PROBE_USER_ID", DEFAULT_PROBE_USER_ID)))
    parser.add_argument("--slot", choices=("morning", "evening"), default=os.getenv("AUTO_AUDIO_PROBE_SLOT", DEFAULT_SLOT))
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    result = run_probe(user_id=int(args.user_id), slot=str(args.slot), keep_artifacts=bool(args.keep_artifacts))
    print(
        f"AUTO_AUDIO_DRY_RUN_OK user_id={result.user_id} slot={result.slot} session_id={result.session_id} "
        f"run_id={result.run_id} cleanup={result.cleanup_status} rows_touched={result.rows_touched}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
