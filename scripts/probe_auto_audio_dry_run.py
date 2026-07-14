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
from services.practice_tokens import grant_tokens, token_access_authoritative
from services.probe_ledger import assert_synthetic_user_id, finish_probe_run, start_probe_run
from services.schema import init_db
from services.subscription import grant, has_access

DEFAULT_PROBE_USER_ID = -910_000_201
DEFAULT_SLOT = "morning"
PROBE_SOURCE = "auto_audio_dry_run_probe"
PROBE_TOKEN_PACKAGE = "probe_auto_audio_single"


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
    """Remove every durable row the synthetic paid-access probe can create."""

    touched = 0
    uid = int(user_id)
    with db() as conn:
        for sql, params in (
            ("DELETE FROM practice_reservations WHERE user_id=?", (uid,)),
            ("DELETE FROM payment_token_grants WHERE user_id=?", (uid,)),
            ("DELETE FROM practice_ledger WHERE user_id=?", (uid,)),
            ("DELETE FROM user_practice_preferences WHERE user_id=?", (uid,)),
            ("DELETE FROM practice_wallets WHERE user_id=?", (uid,)),
            ("DELETE FROM mood_sessions WHERE user_id=? AND source=?", (uid, PROBE_SOURCE)),
            ("DELETE FROM subscriptions WHERE user_id=?", (uid,)),
            ("DELETE FROM idempotency WHERE user_id=?", (uid,)),
            ("DELETE FROM account_channel_identities WHERE account_id=?", (uid,)),
            ("DELETE FROM accounts WHERE account_id=?", (uid,)),
            ("DELETE FROM users WHERE user_id=?", (uid,)),
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


def _grant_probe_access(*, user_id: int, run_id: str) -> str:
    """Grant access through the authority currently used by production."""

    uid = int(user_id)
    if token_access_authoritative():
        inserted, wallet, _ = grant_tokens(
            uid,
            package_id=PROBE_TOKEN_PACKAGE,
            amount=1,
            provider="probe",
            provider_payment_id=run_id,
            source=PROBE_SOURCE,
            idempotency_key=f"probe:auto_audio:{run_id}",
        )
        if not inserted or int(wallet.available_tokens) < 1:
            raise SystemExit("AUTO_AUDIO_DRY_RUN_FAILED canonical token grant was not persisted")
        return "practice_tokens"

    grant(uid, "both", 1, source=PROBE_SOURCE)
    return "legacy_subscription"


def _record_failure(
    *,
    run_id: str,
    rows_touched: int,
    keep_artifacts: bool,
    error: BaseException,
    slot: str,
    access_backend: str,
) -> None:
    finish_probe_run(
        run_id=run_id,
        status="failed",
        cleanup_status="failed" if keep_artifacts else "unknown",
        rows_touched=rows_touched,
        error=str(error),
        evidence={"slot": slot, "access_backend": access_backend},
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
    access_backend = "not_granted"

    try:
        rows_touched += _cleanup_probe_rows(user_id=int(user_id))

        _ensure_probe_user(user_id=int(user_id))
        rows_touched += 1

        access_backend = _grant_probe_access(user_id=int(user_id), run_id=run_id)
        rows_touched += 1
        if not has_access(int(user_id), slot):
            raise SystemExit(
                f"AUTO_AUDIO_DRY_RUN_FAILED paid access was not visible backend={access_backend}"
            )

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
            evidence={
                "slot": slot,
                "session_id": int(session_id),
                "anchor_id": int(audio.anchor),
                "access_backend": access_backend,
            },
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
        _record_failure(
            run_id=run_id,
            rows_touched=rows_touched,
            keep_artifacts=keep_artifacts,
            error=exc,
            slot=slot,
            access_backend=access_backend,
        )
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
