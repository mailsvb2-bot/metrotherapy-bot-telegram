from __future__ import annotations

from services.db import get_connection
from services.db.runtime import CONFIG

READY_TABLES = {
    'users',
    'jobs',
    'plans',
    'payments',
    'schema_migrations',
    'practice_wallets',
    'payment_token_grants',
    'premium_entitlements',
    'premium_delivery_outbox',
    'consultation_requests',
}


def required_readiness_tables() -> list[str]:
    return sorted(READY_TABLES)


def schema_readiness() -> tuple[bool, str | None]:
    required_tables = set(READY_TABLES)
    placeholders = ','.join('?' for _ in sorted(required_tables))
    try:
        with get_connection() as conn:
            if CONFIG.uses_postgres:
                rows = conn.execute(
                    f"SELECT table_name AS name FROM information_schema.tables "
                    f"WHERE table_schema=current_schema() AND table_name IN ({placeholders})",
                    tuple(sorted(required_tables)),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
                    tuple(sorted(required_tables)),
                ).fetchall()
        names: set[str] = set()
        for row in rows:
            if isinstance(row, dict):
                value = row.get('table_name') or row.get('name')
            else:
                try:
                    value = row[0]
                except Exception:  # validator: allow-wide-except
                    value = None
            if value:
                names.add(str(value))
        missing = sorted(required_tables - names)
        if missing:
            return False, 'schema_missing:' + ','.join(missing)
        return True, None
    except Exception as exc:  # validator: allow-wide-except
        return False, f'schema:{exc}'
