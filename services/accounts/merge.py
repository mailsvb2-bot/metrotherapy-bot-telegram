from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.time_utils import utc_now
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


def _ensure_account_conn(conn, account_id: int, *, status: str = "active") -> None:
    now = _iso_now()
    conn.execute(
        """
        INSERT INTO accounts(account_id, primary_user_id, status, created_at, updated_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(account_id) DO UPDATE SET
            primary_user_id=COALESCE(accounts.primary_user_id, excluded.primary_user_id),
            status=COALESCE(accounts.status, excluded.status),
            updated_at=excluded.updated_at
        """.strip(),
        (int(account_id), int(account_id), str(status or "active"), now, now),
    )


def _link_channel_to_account_conn(
    conn,
    *,
    account_id: int,
    platform: str,
    external_user_id: str | None,
    username: str | None = None,
    display_name: str | None = None,
    link_source: str = "account_merge",
) -> None:
    ext = (external_user_id or "").strip()
    if not ext:
        _ensure_account_conn(conn, int(account_id))
        return
    norm = normalize_platform(platform)
    now = _iso_now()
    _ensure_account_conn(conn, int(account_id))
    conn.execute(
        "DELETE FROM account_channel_identities WHERE platform=? AND external_user_id=? AND account_id<>?",
        (norm, ext, int(account_id)),
    )
    conn.execute(
        """
        INSERT INTO account_channel_identities(
            account_id, platform, external_user_id, username, display_name,
            linked_at, last_seen_at, verified_at, link_source
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(account_id, platform) DO UPDATE SET
            external_user_id=excluded.external_user_id,
            username=COALESCE(excluded.username, account_channel_identities.username),
            display_name=COALESCE(excluded.display_name, account_channel_identities.display_name),
            last_seen_at=excluded.last_seen_at,
            verified_at=COALESCE(account_channel_identities.verified_at, excluded.verified_at),
            link_source=excluded.link_source
        """.strip(),
        (
            int(account_id),
            norm,
            ext,
            (username or "").strip() or None,
            (display_name or "").strip() or None,
            now,
            now,
            now,
            (link_source or "account_merge").strip() or "account_merge",
        ),
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
        _link_channel_to_account_conn(
            conn,
            account_id=int(target),
            platform=platform,
            external_user_id=external_user_id,
            username=username,
            display_name=display_name,
        )
        if external_user_id:
            conn.execute(
                "DELETE FROM user_channel_identities WHERE platform=? AND external_user_id=? AND user_id<>?",
                (platform, external_user_id, int(target)),
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
        _link_channel_to_account_conn(
            conn,
            account_id=int(target),
            platform=normalize_platform(row["platform"]),
            external_user_id=row["external_user_id"],
            username=row["username"],
            display_name=row["display_name"],
        )
    conn.execute("DELETE FROM account_channel_identities WHERE account_id=?", (int(source),))


def _ensure_audio_progress_conn(conn, *, account_id: int, product_id: str, program_id: str) -> None:
    conn.execute(
        """
        INSERT INTO account_audio_progress(
            account_id, product_id, program_id,
            last_sent_audio_no, last_completed_audio_no, pending_audio_no, updated_at
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(account_id, product_id, program_id) DO NOTHING
        """.strip(),
        (int(account_id), product_id, program_id, 0, 0, None, _iso_now()),
    )


def _audio_progress_row(conn, *, account_id: int, product_id: str, program_id: str):
    _ensure_audio_progress_conn(conn, account_id=int(account_id), product_id=product_id, program_id=program_id)
    return conn.execute(
        """
        SELECT last_sent_audio_no, last_completed_audio_no, pending_audio_no
        FROM account_audio_progress
        WHERE account_id=? AND product_id=? AND program_id=?
        """.strip(),
        (int(account_id), product_id, program_id),
    ).fetchone()


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
        target_row = _audio_progress_row(conn, account_id=int(target), product_id=product_id, program_id=program_id)
        target_pending = target_row["pending_audio_no"]
        source_pending = row["pending_audio_no"]
        new_completed = max(int(target_row["last_completed_audio_no"] or 0), int(row["last_completed_audio_no"] or 0))
        new_sent = max(
            int(target_row["last_sent_audio_no"] or 0),
            int(row["last_sent_audio_no"] or 0),
            new_completed,
            int(target_pending or 0),
            int(source_pending or 0),
        )
        pending_candidates = [int(value) for value in [target_pending, source_pending] if value is not None]
        pending_candidates = [value for value in pending_candidates if value > new_completed]
        new_pending = max(pending_candidates) if pending_candidates else None
        conn.execute(
            """
            UPDATE account_audio_progress
            SET last_sent_audio_no=?,
                last_completed_audio_no=?,
                pending_audio_no=?,
                updated_at=?
            WHERE account_id=? AND product_id=? AND program_id=?
            """.strip(),
            (new_sent, new_completed, new_pending, _iso_now(), int(target), product_id, program_id),
        )
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
    with db() as conn:
        with tx(conn):
            _ensure_account_conn(conn, plan.target_account_id)
            for source in plan.source_account_ids:
                _ensure_account_conn(conn, int(source))
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
