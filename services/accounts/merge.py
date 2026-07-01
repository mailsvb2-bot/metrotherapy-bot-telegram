from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.time_utils import utc_now
from services.accounts.audio_progress import get_audio_state, mark_audio_completed, mark_audio_sent
from services.accounts.identity import ensure_account, link_channel_to_account
from services.db import db, tx
from services.messenger.platforms import normalize_platform


def _iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class AccountMergePlan:
    target_account_id: int
    source_account_ids: list[int]
    legacy_identity_counts: dict[int, int]
    account_identity_counts: dict[int, int]
    account_audio_progress_counts: dict[int, int]
    account_delivery_counts: dict[int, int]
    account_completion_counts: dict[int, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_account_id": self.target_account_id,
            "source_account_ids": list(self.source_account_ids),
            "legacy_identity_counts": {str(k): v for k, v in self.legacy_identity_counts.items()},
            "account_identity_counts": {str(k): v for k, v in self.account_identity_counts.items()},
            "account_audio_progress_counts": {str(k): v for k, v in self.account_audio_progress_counts.items()},
            "account_delivery_counts": {str(k): v for k, v in self.account_delivery_counts.items()},
            "account_completion_counts": {str(k): v for k, v in self.account_completion_counts.items()},
        }


def _count(conn, table: str, column: str, value: int) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {column}=?", (int(value),)).fetchone()
    return int(row["c"] if row else 0)


def _counts(conn, table: str, column: str, ids: list[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for value in ids:
        count = _count(conn, table, column, int(value))
        if count:
            out[int(value)] = count
    return out


def build_account_merge_plan(target_account_id: int, source_account_ids: list[int]) -> AccountMergePlan:
    target = int(target_account_id)
    sources = [int(source) for source in source_account_ids if int(source) != target]
    if not sources:
        raise ValueError("source_account_ids must contain at least one id different from target_account_id")
    ids = [target, *sources]
    with db() as conn:
        return AccountMergePlan(
            target_account_id=target,
            source_account_ids=sources,
            legacy_identity_counts=_counts(conn, "user_channel_identities", "user_id", ids),
            account_identity_counts=_counts(conn, "account_channel_identities", "account_id", ids),
            account_audio_progress_counts=_counts(conn, "account_audio_progress", "account_id", ids),
            account_delivery_counts=_counts(conn, "account_audio_deliveries", "account_id", ids),
            account_completion_counts=_counts(conn, "account_audio_completions", "account_id", ids),
        )


def _merge_legacy_identities(conn, *, target: int, source: int) -> None:
    rows = conn.execute(
        """
        SELECT platform, external_user_id, username, display_name, first_seen_at, last_seen_at
        FROM user_channel_identities
        WHERE user_id=?
        ORDER BY last_seen_at ASC
        """.strip(),
        (int(source),),
    ).fetchall()
    for row in rows:
        platform = normalize_platform(row["platform"])
        external_user_id = (row["external_user_id"] or "").strip() or None
        username = (row["username"] or "").strip() or None
        display_name = (row["display_name"] or "").strip() or None
        link_channel_to_account(
            int(target),
            platform,
            external_user_id,
            username=username,
            display_name=display_name,
            verified=True,
            link_source="account_merge",
            replace_existing=True,
        )
        conn.execute(
            "DELETE FROM user_channel_identities WHERE user_id=? AND platform=?",
            (int(target), platform),
        )
        conn.execute(
            """
            INSERT INTO user_channel_identities(
                user_id, platform, external_user_id, username, display_name, first_seen_at, last_seen_at
            ) VALUES(?,?,?,?,?,?,?)
            """.strip(),
            (
                int(target),
                platform,
                external_user_id,
                username,
                display_name,
                row["first_seen_at"],
                row["last_seen_at"],
            ),
        )
    conn.execute("DELETE FROM user_channel_identities WHERE user_id=?", (int(source),))


def _merge_legacy_preferences(conn, *, target: int, source: int) -> None:
    source_row = conn.execute(
        "SELECT preferred_platform, last_seen_platform, updated_at FROM user_channel_preferences WHERE user_id=?",
        (int(source),),
    ).fetchone()
    if source_row is None:
        return
    conn.execute(
        """
        INSERT INTO user_channel_preferences(user_id, preferred_platform, last_seen_platform, updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            preferred_platform=excluded.preferred_platform,
            last_seen_platform=excluded.last_seen_platform,
            updated_at=excluded.updated_at
        """.strip(),
        (
            int(target),
            normalize_platform(source_row["preferred_platform"]),
            normalize_platform(source_row["last_seen_platform"]),
            source_row["updated_at"],
        ),
    )
    conn.execute("DELETE FROM user_channel_preferences WHERE user_id=?", (int(source),))


def _merge_account_identities(conn, *, target: int, source: int) -> None:
    rows = conn.execute(
        """
        SELECT platform, external_user_id, username, display_name
        FROM account_channel_identities
        WHERE account_id=?
        """.strip(),
        (int(source),),
    ).fetchall()
    for row in rows:
        link_channel_to_account(
            int(target),
            normalize_platform(row["platform"]),
            row["external_user_id"],
            username=row["username"],
            display_name=row["display_name"],
            verified=True,
            link_source="account_merge",
            replace_existing=True,
        )
    conn.execute("DELETE FROM account_channel_identities WHERE account_id=?", (int(source),))


def _merge_audio_progress(conn, *, target: int, source: int) -> None:
    rows = conn.execute(
        """
        SELECT product_id, program_id, last_sent_audio_no, last_completed_audio_no, pending_audio_no
        FROM account_audio_progress
        WHERE account_id=?
        """.strip(),
        (int(source),),
    ).fetchall()
    for row in rows:
        product_id = str(row["product_id"])
        program_id = str(row["program_id"])
        state = get_audio_state(int(target), product_id=product_id, program_id=program_id)
        last_sent = int(row["last_sent_audio_no"] or 0)
        last_completed = int(row["last_completed_audio_no"] or 0)
        pending = row["pending_audio_no"]
        if last_sent > state.last_sent_audio_no:
            mark_audio_sent(int(target), last_sent, platform="merge", product_id=product_id, program_id=program_id)
        if last_completed > get_audio_state(int(target), product_id=product_id, program_id=program_id).last_completed_audio_no:
            mark_audio_completed(
                int(target),
                last_completed,
                platform="merge",
                product_id=product_id,
                program_id=program_id,
                confirmation_type="account_merge",
            )
        if pending is not None:
            current = get_audio_state(int(target), product_id=product_id, program_id=program_id)
            if int(pending) > current.last_completed_audio_no:
                mark_audio_sent(int(target), int(pending), platform="merge", product_id=product_id, program_id=program_id)
    conn.execute("DELETE FROM account_audio_progress WHERE account_id=?", (int(source),))


def _move_account_deliveries(conn, *, target: int, source: int) -> None:
    conn.execute("UPDATE account_audio_deliveries SET account_id=? WHERE account_id=?", (int(target), int(source)))


def _merge_account_completions(conn, *, target: int, source: int) -> None:
    rows = conn.execute(
        """
        SELECT product_id, program_id, audio_no, source_platform, confirmation_type, completed_at
        FROM account_audio_completions
        WHERE account_id=?
        """.strip(),
        (int(source),),
    ).fetchall()
    for row in rows:
        conn.execute(
            """
            INSERT INTO account_audio_completions(
                account_id, product_id, program_id, audio_no, source_platform, confirmation_type, completed_at
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(account_id, product_id, program_id, audio_no) DO UPDATE SET
                completed_at=CASE
                    WHEN account_audio_completions.completed_at > excluded.completed_at
                    THEN account_audio_completions.completed_at
                    ELSE excluded.completed_at
                END,
                source_platform=excluded.source_platform,
                confirmation_type=excluded.confirmation_type
            """.strip(),
            (
                int(target),
                row["product_id"],
                row["program_id"],
                row["audio_no"],
                row["source_platform"],
                row["confirmation_type"],
                row["completed_at"],
            ),
        )
    conn.execute("DELETE FROM account_audio_completions WHERE account_id=?", (int(source),))


def apply_account_merge(target_account_id: int, source_account_ids: list[int], *, reason: str = "manual") -> AccountMergePlan:
    plan = build_account_merge_plan(int(target_account_id), source_account_ids)
    now = _iso_now()
    ensure_account(plan.target_account_id)
    with db() as conn:
        with tx(conn):
            for source in plan.source_account_ids:
                _merge_legacy_identities(conn, target=plan.target_account_id, source=int(source))
                _merge_legacy_preferences(conn, target=plan.target_account_id, source=int(source))
                _merge_account_identities(conn, target=plan.target_account_id, source=int(source))
                _merge_audio_progress(conn, target=plan.target_account_id, source=int(source))
                _move_account_deliveries(conn, target=plan.target_account_id, source=int(source))
                _merge_account_completions(conn, target=plan.target_account_id, source=int(source))
                conn.execute(
                    "UPDATE accounts SET status=?, updated_at=? WHERE account_id=?",
                    ("merged", now, int(source)),
                )
                conn.execute(
                    """
                    INSERT INTO account_merge_log(
                        target_account_id, source_account_id, mode, status, evidence_json, created_at
                    ) VALUES(?,?,?,?,?,?)
                    """.strip(),
                    (
                        plan.target_account_id,
                        int(source),
                        str(reason),
                        "applied",
                        json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
    return plan
