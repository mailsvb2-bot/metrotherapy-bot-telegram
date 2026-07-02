from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.time_utils import utc_now
from services.accounts.audio_progress import DEFAULT_PRODUCT_ID, get_audio_state
from services.accounts.identity import ensure_account
from services.db import db, tx


def _iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class AccountAudioProgressBackfillPlan:
    target_account_id: int
    source_user_ids: list[int]
    product_id: str
    program_id: str
    legacy_last_completed_audio_no: int
    legacy_pending_audio_no: int | None
    existing_last_sent_audio_no: int
    existing_last_completed_audio_no: int
    existing_pending_audio_no: int | None
    planned_last_sent_audio_no: int
    planned_last_completed_audio_no: int
    planned_pending_audio_no: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_account_id": int(self.target_account_id),
            "source_user_ids": [int(value) for value in self.source_user_ids],
            "product_id": self.product_id,
            "program_id": self.program_id,
            "legacy_last_completed_audio_no": int(self.legacy_last_completed_audio_no),
            "legacy_pending_audio_no": self.legacy_pending_audio_no,
            "existing_last_sent_audio_no": int(self.existing_last_sent_audio_no),
            "existing_last_completed_audio_no": int(self.existing_last_completed_audio_no),
            "existing_pending_audio_no": self.existing_pending_audio_no,
            "planned_last_sent_audio_no": int(self.planned_last_sent_audio_no),
            "planned_last_completed_audio_no": int(self.planned_last_completed_audio_no),
            "planned_pending_audio_no": self.planned_pending_audio_no,
        }


def _legacy_rows(source_user_ids: list[int], *, program_id: str) -> list[dict[str, Any]]:
    if not source_user_ids:
        return []
    placeholders = ",".join("?" for _ in source_user_ids)
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT
                user_id,
                sequence_key,
                last_anchor,
                pending_anchor,
                updated_at
            FROM user_audio_progress
            WHERE user_id IN ({placeholders})
              AND sequence_key=?
            ORDER BY user_id, sequence_key
            """.strip(),
            (*[int(value) for value in source_user_ids], str(program_id)),
        ).fetchall()
    return [dict(row) for row in rows]


def build_account_audio_progress_backfill_plan(
    target_account_id: int,
    source_user_ids: list[int],
    *,
    product_id: str = DEFAULT_PRODUCT_ID,
    program_id: str = "full_series",
) -> AccountAudioProgressBackfillPlan:
    target = ensure_account(int(target_account_id))
    sources = [int(value) for value in source_user_ids]
    if not sources:
        raise ValueError("source_user_ids must not be empty")

    legacy = _legacy_rows(sources, program_id=program_id)
    legacy_last = 0
    legacy_pending = 0
    for row in legacy:
        legacy_last = max(legacy_last, int(row.get("last_anchor") or 0))
        legacy_pending = max(legacy_pending, int(row.get("pending_anchor") or 0))

    existing = get_audio_state(target, product_id=product_id, program_id=program_id)

    planned_completed = max(int(existing.last_completed_audio_no or 0), legacy_last)

    pending_candidates = [
        value
        for value in [
            int(existing.pending_audio_no) if existing.pending_audio_no is not None else None,
            legacy_pending if legacy_pending > 0 else None,
        ]
        if value is not None and int(value) > planned_completed
    ]
    planned_pending = max(pending_candidates) if pending_candidates else None

    planned_sent = max(
        int(existing.last_sent_audio_no or 0),
        planned_completed,
        int(existing.pending_audio_no or 0),
        legacy_pending,
    )

    return AccountAudioProgressBackfillPlan(
        target_account_id=target,
        source_user_ids=sources,
        product_id=str(product_id),
        program_id=str(program_id),
        legacy_last_completed_audio_no=legacy_last,
        legacy_pending_audio_no=legacy_pending if legacy_pending > legacy_last else None,
        existing_last_sent_audio_no=int(existing.last_sent_audio_no or 0),
        existing_last_completed_audio_no=int(existing.last_completed_audio_no or 0),
        existing_pending_audio_no=existing.pending_audio_no,
        planned_last_sent_audio_no=planned_sent,
        planned_last_completed_audio_no=planned_completed,
        planned_pending_audio_no=planned_pending,
    )


def apply_account_audio_progress_backfill(
    target_account_id: int,
    source_user_ids: list[int],
    *,
    product_id: str = DEFAULT_PRODUCT_ID,
    program_id: str = "full_series",
) -> AccountAudioProgressBackfillPlan:
    plan = build_account_audio_progress_backfill_plan(
        int(target_account_id),
        source_user_ids,
        product_id=product_id,
        program_id=program_id,
    )
    now = _iso_now()
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                INSERT INTO account_audio_progress(
                    account_id, product_id, program_id,
                    last_sent_audio_no, last_completed_audio_no, pending_audio_no, updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(account_id, product_id, program_id) DO UPDATE SET
                    last_sent_audio_no=excluded.last_sent_audio_no,
                    last_completed_audio_no=excluded.last_completed_audio_no,
                    pending_audio_no=excluded.pending_audio_no,
                    updated_at=excluded.updated_at
                """.strip(),
                (
                    int(plan.target_account_id),
                    plan.product_id,
                    plan.program_id,
                    int(plan.planned_last_sent_audio_no),
                    int(plan.planned_last_completed_audio_no),
                    plan.planned_pending_audio_no,
                    now,
                ),
            )
    return plan


def plan_to_json_payload(mode: str, plan: AccountAudioProgressBackfillPlan) -> str:
    return json.dumps({"mode": mode, "plan": plan.to_dict()}, ensure_ascii=False, indent=2, sort_keys=True)
