from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.time_utils import utc_now
from services.db import db, tx
from services.messenger.platforms import normalize_platform, parse_platform


def _iso_now() -> str:
    return utc_now().replace(microsecond=0).isoformat()


class AccountIdentityConflict(RuntimeError):
    """Raised when one external messenger identity already belongs to another account."""


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: int
    status: str
    identities: list[dict[str, Any]]


def ensure_account(account_id: int, *, primary_user_id: int | None = None, status: str = "active") -> int:
    aid = int(account_id)
    primary = int(primary_user_id if primary_user_id is not None else aid)
    now = _iso_now()
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                INSERT INTO accounts(account_id, primary_user_id, status, created_at, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(account_id) DO UPDATE SET
                    primary_user_id=COALESCE(accounts.primary_user_id, excluded.primary_user_id),
                    status=COALESCE(accounts.status, excluded.status),
                    updated_at=excluded.updated_at
                """.strip(),
                (aid, primary, str(status or "active"), now, now),
            )
    return aid


def _identity_row(platform: str, external_user_id: str):
    with db() as conn:
        return conn.execute(
            """
            SELECT account_id, platform, external_user_id, username, display_name, linked_at, last_seen_at
            FROM account_channel_identities
            WHERE platform=? AND external_user_id=?
            LIMIT 1
            """.strip(),
            (platform, external_user_id),
        ).fetchone()


def _proposed_account_id(proposed_user_id: int | None, external_user_id: str | None) -> int:
    if proposed_user_id is not None:
        return int(proposed_user_id)
    raw = (external_user_id or "").strip()
    if raw.isdigit():
        return int(raw)
    raise ValueError("proposed_user_id is required for first-time nonnumeric messenger identities")


def link_channel_to_account(
    account_id: int,
    platform: str,
    external_user_id: str | None,
    *,
    username: str | None = None,
    display_name: str | None = None,
    verified: bool = False,
    link_source: str = "runtime",
    replace_existing: bool = False,
) -> int:
    norm = parse_platform(platform)
    if norm is None:
        raise ValueError("invalid platform")
    ext = (external_user_id or "").strip()
    if not ext:
        ensure_account(int(account_id))
        return int(account_id)

    existing = _identity_row(norm, ext)
    if existing is not None and int(existing["account_id"]) != int(account_id):
        if not replace_existing:
            raise AccountIdentityConflict(
                f"{norm}:{ext} already belongs to account_id={int(existing['account_id'])}"
            )
        with db() as conn:
            with tx(conn):
                conn.execute(
                    "DELETE FROM account_channel_identities WHERE platform=? AND external_user_id=?",
                    (norm, ext),
                )

    aid = ensure_account(int(account_id))
    now = _iso_now()
    verified_at = now if verified else None
    uname = (username or "").strip() or None
    dname = (display_name or "").strip() or None
    source = (link_source or "runtime").strip() or "runtime"

    with db() as conn:
        with tx(conn):
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
                (aid, norm, ext, uname, dname, now, now, verified_at, source),
            )
    return aid


def resolve_account_for_identity(
    platform: str,
    external_user_id: str | None,
    *,
    proposed_user_id: int | None = None,
    username: str | None = None,
    display_name: str | None = None,
    allow_create: bool = True,
) -> int | None:
    norm = parse_platform(platform)
    if norm is None:
        raise ValueError("invalid platform")
    ext = (external_user_id or "").strip()
    if ext:
        existing = _identity_row(norm, ext)
        if existing is not None:
            aid = int(existing["account_id"])
            link_channel_to_account(
                aid,
                norm,
                ext,
                username=username,
                display_name=display_name,
                link_source="seen_again",
            )
            return aid
    if not allow_create:
        return None
    aid = _proposed_account_id(proposed_user_id, ext)
    return link_channel_to_account(
        aid,
        norm,
        ext,
        username=username,
        display_name=display_name,
        link_source="first_seen",
    )


def get_account_snapshot(account_id: int) -> dict[str, Any]:
    aid = int(account_id)
    with db() as conn:
        account = conn.execute(
            "SELECT account_id, primary_user_id, status, created_at, updated_at FROM accounts WHERE account_id=?",
            (aid,),
        ).fetchone()
        identities = conn.execute(
            """
            SELECT platform, external_user_id, username, display_name, linked_at, last_seen_at, verified_at, link_source
            FROM account_channel_identities
            WHERE account_id=?
            ORDER BY last_seen_at DESC
            """.strip(),
            (aid,),
        ).fetchall()
    return {
        "account_id": aid,
        "primary_user_id": int(account["primary_user_id"]) if account else aid,
        "status": normalize_platform(account["status"]) if account and str(account["status"] or "") in {"telegram", "vk", "max"} else (account["status"] if account else "missing"),
        "created_at": account["created_at"] if account else None,
        "updated_at": account["updated_at"] if account else None,
        "identities": [dict(row) for row in identities],
    }
