from __future__ import annotations

"""Synthetic local probe for the auto-audio pre-score path.

The historical filename contains ``dry_run`` because the probe never sends a
Telegram message or calls a provider. It does mutate synthetic rows in the
active DB, so callers must now authorize that mutation explicitly.
"""

import argparse
import json
import sqlite3
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.audio_anchor import pick_for_slot
from services.db import db, mark_delivery_once, was_delivered
from services.idempotency_keys import for_pre_score
from services.mood import create_session, get_session
from services.practice_tokens import grant_tokens, token_access_authoritative
from services.probe_ledger import assert_synthetic_user_id, finish_probe_run, start_probe_run
from services.probe_safety import (
    ProbeInvariantError,
    ProbeMutationAuthorizationRequired,
    new_synthetic_user_id,
    require_live_db_mutation,
    safe_probe_error_code,
)
from services.schema import init_db
from services.subscription import grant, has_access

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
    residual_rows: int
    rows_touched: int


def _kind_for_slot(slot: str) -> str:
    return "work" if slot == "morning" else "home"


def _delete_with_count(conn: Any, sql: str, params: tuple[Any, ...]) -> int:
    cur = conn.execute(sql, params)
    return max(int(getattr(cur, "rowcount", 0) or 0), 0)


def _cleanup_probe_rows(*, user_id: int) -> int:
    """Remove every durable row the reserved synthetic identity can create."""

    assert_synthetic_user_id(int(user_id))
    uid = int(user_id)
    external_uid = str(uid)
    touched = 0
    with db() as conn:
        statements = (
            ("DELETE FROM practice_reservations WHERE user_id=?", (uid,)),
            ("DELETE FROM payment_token_grants WHERE user_id=?", (uid,)),
            ("DELETE FROM practice_ledger WHERE user_id=?", (uid,)),
            ("DELETE FROM user_practice_preferences WHERE user_id=?", (uid,)),
            ("DELETE FROM practice_wallets WHERE user_id=?", (uid,)),
            ("DELETE FROM mood_sessions WHERE user_id=? AND source=?", (uid, PROBE_SOURCE)),
            ("DELETE FROM subscriptions WHERE user_id=?", (uid,)),
            ("DELETE FROM idempotency WHERE user_id=?", (uid,)),
            ("DELETE FROM account_audio_completions WHERE account_id=?", (uid,)),
            ("DELETE FROM account_audio_deliveries WHERE account_id=?", (uid,)),
            ("DELETE FROM account_audio_progress WHERE account_id=?", (uid,)),
            (
                "DELETE FROM account_channel_identities WHERE account_id=? OR external_user_id=?",
                (uid, external_uid),
            ),
            ("DELETE FROM accounts WHERE account_id=? OR primary_user_id=?", (uid, uid)),
            ("DELETE FROM users WHERE user_id=?", (uid,)),
        )
        for sql, params in statements:
            touched += _delete_with_count(conn, sql, params)
    return touched


def _residual_rows(*, user_id: int) -> int:
    assert_synthetic_user_id(int(user_id))
    uid = int(user_id)
    external_uid = str(uid)
    with db() as conn:
        queries = (
            ("SELECT COUNT(*) FROM practice_reservations WHERE user_id=?", (uid,)),
            ("SELECT COUNT(*) FROM payment_token_grants WHERE user_id=?", (uid,)),
            ("SELECT COUNT(*) FROM practice_ledger WHERE user_id=?", (uid,)),
            ("SELECT COUNT(*) FROM user_practice_preferences WHERE user_id=?", (uid,)),
            ("SELECT COUNT(*) FROM practice_wallets WHERE user_id=?", (uid,)),
            ("SELECT COUNT(*) FROM mood_sessions WHERE user_id=? AND source=?", (uid, PROBE_SOURCE)),
            ("SELECT COUNT(*) FROM subscriptions WHERE user_id=?", (uid,)),
            ("SELECT COUNT(*) FROM idempotency WHERE user_id=?", (uid,)),
            ("SELECT COUNT(*) FROM account_audio_completions WHERE account_id=?", (uid,)),
            ("SELECT COUNT(*) FROM account_audio_deliveries WHERE account_id=?", (uid,)),
            ("SELECT COUNT(*) FROM account_audio_progress WHERE account_id=?", (uid,)),
            (
                "SELECT COUNT(*) FROM account_channel_identities WHERE account_id=? OR external_user_id=?",
                (uid, external_uid),
            ),
            ("SELECT COUNT(*) FROM accounts WHERE account_id=? OR primary_user_id=?", (uid, uid)),
            ("SELECT COUNT(*) FROM users WHERE user_id=?", (uid,)),
        )
        total = 0
        for sql, params in queries:
            row = conn.execute(sql, params).fetchone()
            total += int(row[0]) if row else 0
        return total


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
            raise ProbeInvariantError("canonical_token_grant_not_persisted")
        return "practice_tokens"

    grant(uid, "both", 1, source=PROBE_SOURCE)
    return "legacy_subscription"


def _safe_finish_failure(
    *,
    run_id: str,
    rows_touched: int,
    cleanup_status: str,
    error: BaseException,
    slot: str,
    access_backend: str,
) -> None:
    try:
        finish_probe_run(
            run_id=run_id,
            status="failed",
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
            error=safe_probe_error_code(error),
            evidence={
                "slot": slot,
                "access_backend": access_backend,
                "error_code": safe_probe_error_code(error),
            },
        )
    except sqlite3.Error:
        return
    except (RuntimeError, ValueError):
        return


def _cleanup_after_failure(
    *,
    user_id: int,
    rows_touched: int,
    keep_artifacts: bool,
) -> tuple[int, str]:
    if keep_artifacts:
        return rows_touched, "kept_failed"
    try:
        touched = rows_touched + _cleanup_probe_rows(user_id=user_id)
        residual = _residual_rows(user_id=user_id)
        return touched, "clean" if residual == 0 else "residual"
    except sqlite3.Error:
        return rows_touched, "cleanup_failed"
    except (RuntimeError, ValueError):
        return rows_touched, "cleanup_failed"


def run_probe(
    *,
    user_id: int,
    slot: str = DEFAULT_SLOT,
    keep_artifacts: bool = False,
    initialize_schema: bool = True,
    allow_live_db_mutation: bool,
) -> AutoAudioProbeResult:
    require_live_db_mutation(allow_live_db_mutation)
    assert_synthetic_user_id(int(user_id))
    if initialize_schema:
        init_db()
    normalized_slot = (slot or DEFAULT_SLOT).strip().lower()
    if normalized_slot not in {"morning", "evening"}:
        raise ValueError("slot_must_be_morning_or_evening")

    run_id = uuid.uuid4().hex
    start_probe_run(
        probe_type=PROBE_SOURCE,
        user_id=int(user_id),
        run_id=run_id,
        evidence={"slot": normalized_slot, "mutation_authorized": True},
    )
    rows_touched = 0
    access_backend = "not_granted"

    try:
        rows_touched += _cleanup_probe_rows(user_id=int(user_id))

        _ensure_probe_user(user_id=int(user_id))
        rows_touched += 1

        access_backend = _grant_probe_access(user_id=int(user_id), run_id=run_id)
        rows_touched += 1
        if not has_access(int(user_id), normalized_slot):
            raise ProbeInvariantError("paid_access_not_visible")

        audio = pick_for_slot(normalized_slot, 0)
        if audio is None:
            raise ProbeInvariantError("anchored_audio_missing")

        local_day = datetime.now(timezone.utc).date().isoformat()
        scheduled_at = for_pre_score(int(user_id), local_day, normalized_slot)
        kind = _kind_for_slot(normalized_slot)

        if not mark_delivery_once(int(user_id), kind, "pre_score", scheduled_at):
            raise ProbeInvariantError("pre_score_idempotency_duplicate")
        rows_touched += 1
        if not was_delivered(int(user_id), kind, "pre_score", scheduled_at):
            raise ProbeInvariantError("pre_score_idempotency_missing")

        session_id = create_session(
            int(user_id),
            kind=kind,
            source=PROBE_SOURCE,
            day=local_day,
            slot=normalized_slot,
            scheduled_at=scheduled_at,
            anchor_id=int(audio.anchor),
        )
        rows_touched += 1
        session = get_session(int(session_id))
        if session is None:
            raise ProbeInvariantError("mood_session_missing")
        if int(session.user_id) != int(user_id):
            raise ProbeInvariantError("session_user_mismatch")
        if session.slot != normalized_slot:
            raise ProbeInvariantError("session_slot_mismatch")
        if int(session.anchor_id or 0) != int(audio.anchor):
            raise ProbeInvariantError("session_anchor_mismatch")

        cleanup_status = "kept"
        residual_rows = _residual_rows(user_id=int(user_id))
        if not keep_artifacts:
            rows_touched += _cleanup_probe_rows(user_id=int(user_id))
            residual_rows = _residual_rows(user_id=int(user_id))
            cleanup_status = "clean" if residual_rows == 0 else "residual"
            if residual_rows:
                raise ProbeInvariantError("cleanup_residual_rows")

        finish_probe_run(
            run_id=run_id,
            status="ok",
            cleanup_status=cleanup_status,
            rows_touched=rows_touched,
            evidence={
                "slot": normalized_slot,
                "session_id": int(session_id),
                "anchor_id": int(audio.anchor),
                "access_backend": access_backend,
                "residual_rows": residual_rows,
            },
        )
        return AutoAudioProbeResult(
            user_id=int(user_id),
            slot=normalized_slot,
            session_id=int(session_id),
            run_id=run_id,
            cleanup_status=cleanup_status,
            residual_rows=residual_rows,
            rows_touched=rows_touched,
        )
    except sqlite3.Error as exc:
        touched, cleanup_status = _cleanup_after_failure(
            user_id=int(user_id),
            rows_touched=rows_touched,
            keep_artifacts=keep_artifacts,
        )
        _safe_finish_failure(
            run_id=run_id,
            rows_touched=touched,
            cleanup_status=cleanup_status,
            error=exc,
            slot=normalized_slot,
            access_backend=access_backend,
        )
        raise
    except RuntimeError as exc:
        touched, cleanup_status = _cleanup_after_failure(
            user_id=int(user_id),
            rows_touched=rows_touched,
            keep_artifacts=keep_artifacts,
        )
        _safe_finish_failure(
            run_id=run_id,
            rows_touched=touched,
            cleanup_status=cleanup_status,
            error=exc,
            slot=normalized_slot,
            access_backend=access_backend,
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe the synthetic auto-audio pre-score local path")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--slot", choices=("morning", "evening"), default=DEFAULT_SLOT)
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--allow-live-db-mutation", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    user_id = int(args.user_id) if args.user_id is not None else new_synthetic_user_id()
    try:
        result = run_probe(
            user_id=user_id,
            slot=str(args.slot),
            keep_artifacts=bool(args.keep_artifacts),
            allow_live_db_mutation=bool(args.allow_live_db_mutation),
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

    if args.json:
        print(json.dumps({"ok": True, "applied": True, **asdict(result)}, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"AUTO_AUDIO_PROBE_OK user_id={result.user_id} slot={result.slot} session_id={result.session_id} "
            f"run_id={result.run_id} cleanup={result.cleanup_status} residual={result.residual_rows} "
            f"rows_touched={result.rows_touched}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
