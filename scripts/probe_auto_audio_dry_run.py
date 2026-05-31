from __future__ import annotations

"""Dry-run probe for the auto-audio pre-score path.

The probe avoids Telegram/network sends. It verifies the production-critical local
path that precedes a scheduled auto-audio prompt:

- grant a synthetic user access;
- resolve an anchored audio item for a slot;
- reserve the pre-score delivery idempotently;
- create a mood session with the selected anchor;
- verify session fields;
- clean up synthetic rows unless requested otherwise.
"""

import argparse
import os
from datetime import datetime, timezone

from services.audio_anchor import pick_for_slot
from services.db import db, mark_delivery_once, was_delivered
from services.idempotency_keys import for_pre_score
from services.mood import create_session, get_session
from services.schema import init_db
from services.subscription import grant, has_access

DEFAULT_PROBE_USER_ID = -910_000_201
DEFAULT_SLOT = "morning"
PROBE_SOURCE = "auto_audio_dry_run_probe"


def _kind_for_slot(slot: str) -> str:
    return "work" if slot == "morning" else "home"


def _cleanup_probe_rows(*, user_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM mood_sessions WHERE user_id=? AND source=?", (int(user_id), PROBE_SOURCE))
        conn.execute("DELETE FROM subscriptions WHERE user_id=?", (int(user_id),))
        conn.execute("DELETE FROM idempotency WHERE user_id=?", (int(user_id),))
        conn.execute("DELETE FROM users WHERE user_id=?", (int(user_id),))


def run_probe(*, user_id: int = DEFAULT_PROBE_USER_ID, slot: str = DEFAULT_SLOT, keep_artifacts: bool = False) -> int:
    init_db()
    slot = (slot or DEFAULT_SLOT).strip().lower()
    if slot not in {"morning", "evening"}:
        raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED slot must be morning or evening")

    _cleanup_probe_rows(user_id=int(user_id))

    # A users row keeps channel/timezone preference code paths deterministic and
    # mirrors real users without requiring a messenger identity.
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users(user_id, work_time, home_time) VALUES(?,?,?)",
            (int(user_id), "08:30", "19:30"),
        )

    grant(int(user_id), "both", 1, source=PROBE_SOURCE)
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
    session = get_session(int(session_id))
    if session is None:
        raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED mood session not found after create_session")
    if int(session.user_id) != int(user_id):
        raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED session user_id mismatch")
    if session.slot != slot:
        raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED session slot mismatch")
    if int(session.anchor_id or 0) != int(audio.anchor):
        raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED session anchor mismatch")

    if not keep_artifacts:
        _cleanup_probe_rows(user_id=int(user_id))

    return int(session_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run probe for auto-audio pre-score local path")
    parser.add_argument("--user-id", type=int, default=int(os.getenv("AUTO_AUDIO_PROBE_USER_ID", DEFAULT_PROBE_USER_ID)))
    parser.add_argument("--slot", choices=("morning", "evening"), default=os.getenv("AUTO_AUDIO_PROBE_SLOT", DEFAULT_SLOT))
    parser.add_argument("--keep-artifacts", action="store_true")
    args = parser.parse_args()

    session_id = run_probe(user_id=int(args.user_id), slot=str(args.slot), keep_artifacts=bool(args.keep_artifacts))
    print(f"AUTO_AUDIO_DRY_RUN_OK user_id={int(args.user_id)} slot={args.slot} session_id={session_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
