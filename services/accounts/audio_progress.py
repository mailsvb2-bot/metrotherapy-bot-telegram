from __future__ import annotations

from dataclasses import dataclass

from core.time_utils import utc_now
from services.accounts.identity import ensure_account
from services.db import db, tx

DEFAULT_PRODUCT_ID = "metrotherapy"
DEFAULT_PROGRAM_ID = "full_series"


def _iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class AccountAudioState:
    account_id: int
    product_id: str
    program_id: str
    last_sent_audio_no: int
    last_completed_audio_no: int
    pending_audio_no: int | None
    updated_at: str

    @property
    def next_audio_no(self) -> int:
        if self.pending_audio_no is not None and self.pending_audio_no > self.last_completed_audio_no:
            return int(self.pending_audio_no)
        return int(self.last_completed_audio_no) + 1


def _row_to_state(row) -> AccountAudioState:
    return AccountAudioState(
        account_id=int(row["account_id"]),
        product_id=str(row["product_id"]),
        program_id=str(row["program_id"]),
        last_sent_audio_no=int(row["last_sent_audio_no"] or 0),
        last_completed_audio_no=int(row["last_completed_audio_no"] or 0),
        pending_audio_no=(int(row["pending_audio_no"]) if row["pending_audio_no"] is not None else None),
        updated_at=str(row["updated_at"]),
    )


def get_audio_state(
    account_id: int,
    *,
    product_id: str = DEFAULT_PRODUCT_ID,
    program_id: str = DEFAULT_PROGRAM_ID,
) -> AccountAudioState:
    aid = ensure_account(int(account_id))
    now = _iso_now()
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                INSERT INTO account_audio_progress(
                    account_id, product_id, program_id,
                    last_sent_audio_no, last_completed_audio_no, pending_audio_no, updated_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(account_id, product_id, program_id) DO NOTHING
                """.strip(),
                (aid, product_id, program_id, 0, 0, None, now),
            )
            row = conn.execute(
                """
                SELECT account_id, product_id, program_id, last_sent_audio_no,
                       last_completed_audio_no, pending_audio_no, updated_at
                FROM account_audio_progress
                WHERE account_id=? AND product_id=? AND program_id=?
                """.strip(),
                (aid, product_id, program_id),
            ).fetchone()
    return _row_to_state(row)


def mark_audio_sent(
    account_id: int,
    audio_no: int,
    *,
    platform: str,
    external_user_id: str | None = None,
    product_id: str = DEFAULT_PRODUCT_ID,
    program_id: str = DEFAULT_PROGRAM_ID,
) -> AccountAudioState:
    aid = ensure_account(int(account_id))
    no = int(audio_no)
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
                    last_sent_audio_no=CASE
                        WHEN account_audio_progress.last_sent_audio_no > excluded.last_sent_audio_no
                        THEN account_audio_progress.last_sent_audio_no
                        ELSE excluded.last_sent_audio_no
                    END,
                    pending_audio_no=CASE
                        WHEN account_audio_progress.last_completed_audio_no >= excluded.pending_audio_no
                        THEN account_audio_progress.pending_audio_no
                        ELSE excluded.pending_audio_no
                    END,
                    updated_at=excluded.updated_at
                """.strip(),
                (aid, product_id, program_id, no, 0, no, now),
            )
            conn.execute(
                """
                INSERT INTO account_audio_deliveries(
                    account_id, product_id, program_id, audio_no,
                    platform, external_user_id, status, sent_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """.strip(),
                (aid, product_id, program_id, no, str(platform), (external_user_id or None), "sent", now, now),
            )
    return get_audio_state(aid, product_id=product_id, program_id=program_id)


def mark_audio_completed(
    account_id: int,
    audio_no: int,
    *,
    platform: str,
    product_id: str = DEFAULT_PRODUCT_ID,
    program_id: str = DEFAULT_PROGRAM_ID,
    confirmation_type: str = "user_clicked_done",
) -> AccountAudioState:
    aid = ensure_account(int(account_id))
    no = int(audio_no)
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
                    last_sent_audio_no=CASE
                        WHEN account_audio_progress.last_sent_audio_no > excluded.last_sent_audio_no
                        THEN account_audio_progress.last_sent_audio_no
                        ELSE excluded.last_sent_audio_no
                    END,
                    last_completed_audio_no=CASE
                        WHEN account_audio_progress.last_completed_audio_no > excluded.last_completed_audio_no
                        THEN account_audio_progress.last_completed_audio_no
                        ELSE excluded.last_completed_audio_no
                    END,
                    pending_audio_no=CASE
                        WHEN account_audio_progress.pending_audio_no <= excluded.last_completed_audio_no
                        THEN NULL
                        ELSE account_audio_progress.pending_audio_no
                    END,
                    updated_at=excluded.updated_at
                """.strip(),
                (aid, product_id, program_id, no, no, None, now),
            )
            conn.execute(
                """
                INSERT INTO account_audio_completions(
                    account_id, product_id, program_id, audio_no,
                    source_platform, confirmation_type, completed_at
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(account_id, product_id, program_id, audio_no) DO UPDATE SET
                    source_platform=excluded.source_platform,
                    confirmation_type=excluded.confirmation_type,
                    completed_at=excluded.completed_at
                """.strip(),
                (aid, product_id, program_id, no, str(platform), confirmation_type, now),
            )
    return get_audio_state(aid, product_id=product_id, program_id=program_id)


def next_audio_no(
    account_id: int,
    *,
    product_id: str = DEFAULT_PRODUCT_ID,
    program_id: str = DEFAULT_PROGRAM_ID,
) -> int:
    return get_audio_state(account_id, product_id=product_id, program_id=program_id).next_audio_no
