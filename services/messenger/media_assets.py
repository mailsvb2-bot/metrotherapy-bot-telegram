from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.time_utils import utc_now
from services.db import db, tx

_DEFAULT_TOKEN_MAX_AGE_SEC = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class MediaAssetToken:
    platform: str
    asset_key: str
    asset_path: Path
    asset_mtime: float
    asset_size: int
    remote_token: str
    media_type: str


def _snapshot(path: Path) -> tuple[float, int]:
    stat = path.stat()
    return float(stat.st_mtime), int(stat.st_size)


def build_asset_key(path: Path) -> str:
    try:
        return path.resolve().as_posix()
    except OSError:
        return str(path)


def _token_max_age_sec(platform: str) -> int:
    platform_name = str(platform or "").strip().upper()
    raw = (
        os.getenv(f"{platform_name}_MEDIA_TOKEN_MAX_AGE_SEC")
        or os.getenv("MESSENGER_MEDIA_TOKEN_MAX_AGE_SEC")
        or str(_DEFAULT_TOKEN_MAX_AGE_SEC)
    ).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TOKEN_MAX_AGE_SEC
    return min(max(value, 0), 365 * 24 * 60 * 60)


def _token_expired(updated_at: object, *, max_age_sec: int) -> bool:
    if max_age_sec <= 0:
        return False
    try:
        parsed = datetime.fromisoformat(str(updated_at or ""))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = (utc_now() - parsed.astimezone(timezone.utc)).total_seconds()
    return age < 0 or age > max_age_sec


def invalidate_media_token(platform: str, path: Path, *, media_type: str = "audio") -> bool:
    asset_key = build_asset_key(path)
    with db() as conn:
        with tx(conn):
            cursor = conn.execute(
                "DELETE FROM messenger_media_assets WHERE platform=? AND asset_key=? AND media_type=?",
                (str(platform), asset_key, str(media_type)),
            )
    return int(getattr(cursor, "rowcount", 0) or 0) > 0


def get_cached_media_token(platform: str, path: Path, *, media_type: str = "audio") -> MediaAssetToken | None:
    if not path.exists() or not path.is_file():
        return None
    asset_key = build_asset_key(path)
    mtime, size = _snapshot(path)
    with db() as conn:
        row = conn.execute(
            """
            SELECT platform, asset_key, asset_path, asset_mtime, asset_size,
                   remote_token, media_type, updated_at
            FROM messenger_media_assets
            WHERE platform=? AND asset_key=? AND media_type=?
            """.strip(),
            (str(platform), asset_key, str(media_type)),
        ).fetchone()
        if row is None:
            return None
        stale_file = float(row["asset_mtime"]) != float(mtime) or int(row["asset_size"]) != int(size)
        expired = _token_expired(
            row["updated_at"],
            max_age_sec=_token_max_age_sec(str(platform)),
        )
        if stale_file or expired or not str(row["remote_token"] or "").strip():
            with tx(conn):
                conn.execute(
                    "DELETE FROM messenger_media_assets WHERE platform=? AND asset_key=? AND media_type=?",
                    (str(platform), asset_key, str(media_type)),
                )
            return None
        now = utc_now().replace(microsecond=0).isoformat()
        with tx(conn):
            conn.execute(
                "UPDATE messenger_media_assets SET last_used_at=? WHERE platform=? AND asset_key=? AND media_type=?",
                (now, str(platform), asset_key, str(media_type)),
            )
    return MediaAssetToken(
        platform=str(row["platform"]),
        asset_key=str(row["asset_key"]),
        asset_path=Path(str(row["asset_path"])),
        asset_mtime=float(row["asset_mtime"]),
        asset_size=int(row["asset_size"]),
        remote_token=str(row["remote_token"]),
        media_type=str(row["media_type"]),
    )


def store_media_token(platform: str, path: Path, remote_token: str, *, media_type: str = "audio") -> MediaAssetToken:
    clean_token = str(remote_token or "").strip()
    if not clean_token:
        raise ValueError("remote media token is required")
    asset_key = build_asset_key(path)
    mtime, size = _snapshot(path)
    now = utc_now().replace(microsecond=0).isoformat()
    with db() as conn:
        with tx(conn):
            conn.execute(
                """
                INSERT INTO messenger_media_assets(
                    platform, asset_key, asset_path, asset_mtime, asset_size,
                    remote_token, media_type, created_at, updated_at, last_used_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(platform, asset_key, media_type) DO UPDATE SET
                    asset_path=excluded.asset_path,
                    asset_mtime=excluded.asset_mtime,
                    asset_size=excluded.asset_size,
                    remote_token=excluded.remote_token,
                    media_type=excluded.media_type,
                    updated_at=excluded.updated_at,
                    last_used_at=excluded.last_used_at
                """.strip(),
                (
                    str(platform),
                    asset_key,
                    str(path),
                    float(mtime),
                    int(size),
                    clean_token,
                    str(media_type),
                    now,
                    now,
                    now,
                ),
            )
    return MediaAssetToken(
        platform=str(platform),
        asset_key=asset_key,
        asset_path=path,
        asset_mtime=float(mtime),
        asset_size=int(size),
        remote_token=clean_token,
        media_type=str(media_type),
    )
