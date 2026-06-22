from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from core.paths import DB_PATH, DATABASE_URL, DB_ENGINE


@dataclass(frozen=True)
class DbRuntimeConfig:
    engine: str
    db_path: Path
    database_url: str

    @property
    def uses_postgres(self) -> bool:
        return self.engine == "postgres"

    @property
    def uses_sqlite(self) -> bool:
        return self.engine == "sqlite"


def _normalize_engine(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"postgres", "postgresql", "pg"}:
        return "postgres"
    return "sqlite"


CONFIG: Final[DbRuntimeConfig] = DbRuntimeConfig(
    engine=_normalize_engine(DB_ENGINE or os.getenv("METRO_DB_ENGINE")),
    db_path=Path(DB_PATH),
    database_url=(DATABASE_URL or "").strip(),
)


def is_postgres_enabled() -> bool:
    return CONFIG.uses_postgres


def is_sqlite_enabled() -> bool:
    return CONFIG.uses_sqlite


def redacted_db_target() -> str:
    if CONFIG.uses_sqlite:
        return str(CONFIG.db_path)
    url = CONFIG.database_url
    if not url:
        return "postgres://<missing>"
    if "@" not in url:
        return url
    prefix, suffix = url.split("@", 1)
    if "://" in prefix:
        scheme, creds = prefix.split("://", 1)
        if ":" in creds:
            user, _ = creds.split(":", 1)
            return f"{scheme}://{user}:***@{suffix}"
        return f"{scheme}://***@{suffix}"
    return f"***@{suffix}"


def postgres_driver_error_hint() -> str:
    return (
        "Postgres mode requires psycopg. Install dependencies from requirements.txt "
        "and set DATABASE_URL, for example: postgresql://metro:secret@127.0.0.1:5432/metrotherapy"
    )
