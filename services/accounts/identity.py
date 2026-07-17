from __future__ import annotations

from dataclasses import dataclass
import hashlib
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


def _ensure_account_in_conn(conn: Any, account_id: int, *, primary_user_id: int | None = None, status: str = "active") -> int:
    aid = int(account_id)
    primary = int(primary_user_id if primary_user_id is not None else aid)
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
        (aid, primary, str(status or "active"), now, now),
    )
    return aid


def ensure_account(account_id: int, *, primary_user_id: int | None = None, status: str = "active") -> int:
    with db() as conn:
        with tx(conn):
            return _ensure_account_in_conn(
                conn,
                int(account_id),
                primary_user_id=primary_user_id,
                status=status,
            )


def _identity_row_in_conn(conn: Any, platform: str, external_user_id: str):
    return conn.execute(
        """
        SELECT account_id, platform, external_user_id, username, display_name, linked_at, last_seen_at
        FROM account_channel_identities
        WHERE platform=? AND external_user_id=?
        LIMIT 1
        """.strip(),
        (platform, external_user_id),
    ).fetchone()


def _identity_row(platform: str, external_user_id: str):
    with db() as conn:
        return _identity_row_in_conn(conn, platform, external_user_id)


def _platform_scoped_account_id(platform: str, external_user_id: str) -> int:
    """Return a stable platform-scoped id for a non-Telegram identity.

    Messenger user identifiers are only unique inside their own platform. They
    must never be reused as global account identifiers, otherwise a VK user and
    a MAX/Telegram user with the same numeric id can be silently merged. A high,
    platform-scoped digest keeps the legacy Telegram id namespace intact while
    making accidental cross-platform equality extremely unlikely.
    """

    raw = f"{platform}:{external_user_id}".encode("utf-8")
    value = int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big")
    # Telegram identifiers fit within 52 significant bits. Reserve the high
    # positive BIGINT range for internal accounts so existing code that requires
    # positive user ids remains valid without sharing Telegram's namespace.
    return (1 << 62) | (value & ((1 << 61) - 1))


def _proposed_account_id(
    platform: str,
    proposed_user_id: int | None,
    external_user_id: str | None,
) -> int:
    norm = parse_platform(platform)
    if norm is None:
        raise ValueError("invalid platform")
    raw = (external_user_id or "").strip()
    if norm == "telegram":
        if proposed_user_id is not None:
            return int(proposed_user_id)
        if raw.isdigit():
            return int(raw)
        raise ValueError("proposed_user_id is required for first-time Telegram identities")
    if not raw:
        raise ValueError("external_user_id is required for first-time non-Telegram identities")
    return _platform_scoped_account_id(norm, raw)


def _link_channel_to_account_in_conn(
    conn: Any,
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
    """Link an identity using the caller's transaction.

    Bridge-token consumption uses this primitive so claiming the token and
    linking the external identity either commit together or both roll back.
    """

    norm = parse_platform(platform)
    if norm is None:
        raise ValueError("invalid platform")
    ext = (external_user_id or "").strip()
    aid = int(account_id)
    now = _iso_now()
    verified_at = now if verified else None
    uname = (username or "").strip() or None
    dname = (display_name or "").strip() or None
    source = (link_source or "runtime").strip() or "runtime"

    if not ext:
        return _ensure_account_in_conn(conn, aid)

    existing = _identity_row_in_conn(conn, norm, ext)
    if existing is not None and int(existing["account_id"]) != aid:
        if not replace_existing:
            raise AccountIdentityConflict(
                f"{norm}:{ext} already belongs to account_id={int(existing['account_id'])}"
            )
        conn.execute(
            "DELETE FROM account_channel_identities WHERE platform=? AND external_user_id=?",
            (norm, ext),
        )

    _ensure_account_in_conn(conn, aid)
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
    """Link one platform identity to an account as a single atomic mutation.

    Replacing an existing owner used to delete the old identity in one transaction
    and create the new owner in another. A failure between those steps orphaned
    the identity. Conflict inspection, optional replacement, account creation and
    the final upsert now share one transaction, so any failure restores the
    original owner.
    """

    with db() as conn:
        with tx(conn):
            return _link_channel_to_account_in_conn(
                conn,
                int(account_id),
                platform,
                external_user_id,
                username=username,
                display_name=display_name,
                verified=verified,
                link_source=link_source,
                replace_existing=replace_existing,
            )


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
    aid = _proposed_account_id(norm, proposed_user_id, ext)
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
