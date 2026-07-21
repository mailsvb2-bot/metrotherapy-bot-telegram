from __future__ import annotations

"""Fail-closed guards for the legacy SQLite-to-PostgreSQL compatibility surface.

The project still accepts SQLite-flavoured statements from older services. This
module owns the safety boundary around that compatibility layer: placeholders
are rewritten lexically, schema probes count only real parameters, and SQLite
PRAGMA statements are rejected before they can be silently treated as a
successful PostgreSQL query.
"""

import re
import sqlite3
from types import ModuleType
from typing import Any, Callable, Sequence


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TABLE_INFO_RE = re.compile(r"(?is)^PRAGMA\s+table_info\(([^)]+)\)\s*;?\s*$")


def rewrite_qmark_placeholders(sql: str) -> tuple[str, int]:
    """Replace real SQLite ``?`` parameters while preserving SQL text.

    This is a deliberately small lexical scanner, not a general SQL parser. It
    distinguishes quoted strings, quoted identifiers and both SQL comment forms
    so a question mark in text or a comment cannot become a psycopg parameter.
    """

    out: list[str] = []
    placeholders = 0
    state = "normal"
    index = 0

    while index < len(sql):
        char = sql[index]
        following = sql[index + 1] if index + 1 < len(sql) else ""

        if state == "single":
            out.append(char)
            if char == "'":
                if following == "'":
                    out.append(following)
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "double":
            out.append(char)
            if char == '"':
                if following == '"':
                    out.append(following)
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "line_comment":
            out.append(char)
            if char in "\r\n":
                state = "normal"
            index += 1
            continue

        if state == "block_comment":
            out.append(char)
            if char == "*" and following == "/":
                out.append(following)
                index += 2
                state = "normal"
                continue
            index += 1
            continue

        if char == "'":
            out.append(char)
            state = "single"
        elif char == '"':
            out.append(char)
            state = "double"
        elif char == "-" and following == "-":
            out.extend((char, following))
            index += 2
            state = "line_comment"
            continue
        elif char == "/" and following == "*":
            out.extend((char, following))
            index += 2
            state = "block_comment"
            continue
        elif char == "?":
            out.append("%s")
            placeholders += 1
        else:
            out.append(char)
        index += 1

    return "".join(out), placeholders


def replace_qmark_placeholders(sql: str) -> str:
    return rewrite_qmark_placeholders(sql)[0]


def count_qmark_placeholders(sql: str) -> int:
    return rewrite_qmark_placeholders(sql)[1]


def translate_sqlite_master_tables_query(sql: str) -> str | None:
    """Translate the supported ``sqlite_master`` table-discovery forms."""

    if not re.match(
        r"(?is)^SELECT\s+(?:name|1)\s+FROM\s+sqlite_master\s+WHERE\s+type='table'",
        sql,
    ):
        return None

    base = (
        "SELECT table_name AS name FROM information_schema.tables "
        "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
    )

    if re.search(r"(?is)\bname\s+IN\s*\(", sql):
        placeholder_count = count_qmark_placeholders(sql)
        if placeholder_count > 0:
            placeholders = ",".join("%s" for _ in range(placeholder_count))
            return f"{base} AND table_name IN ({placeholders})"

    if re.search(r"(?is)\bname\s*=\s*\?", sql):
        return f"{base} AND table_name=%s LIMIT 1"

    if re.search(r"(?is)\bname\s+NOT\s+LIKE\s+'sqlite_%'", sql):
        return f"{base} AND table_name NOT LIKE 'sqlite_%'"

    return base


def validate_sqlite_compat_statement(sql: str) -> None:
    """Reject unsupported or malformed PRAGMA statements before translation."""

    statement = str(sql or "").strip()
    if not statement.upper().startswith("PRAGMA "):
        return

    table_info = _TABLE_INFO_RE.match(statement)
    if table_info is not None:
        table = table_info.group(1).strip().strip('"`[]')
        if _IDENTIFIER_RE.fullmatch(table) is None:
            raise sqlite3.OperationalError(
                "invalid PRAGMA table_info identifier for PostgreSQL compatibility"
            )
        return

    pragma_name = statement[7:].split("(", 1)[0].split("=", 1)[0].strip().rstrip(";")
    safe_name = pragma_name if _IDENTIFIER_RE.fullmatch(pragma_name) is not None else "unknown"
    raise sqlite3.OperationalError(
        f"unsupported SQLite PRAGMA for PostgreSQL compatibility: {safe_name}"
    )


def install_sql_compat_guards(core_module: ModuleType) -> None:
    """Install the guards once on the canonical DB compatibility module.

    Existing services import :mod:`services.db.core` directly, so the package
    keeps that public surface while replacing the unsafe helper implementations
    and adding validation at both cursor execution entry points.
    """

    if bool(getattr(core_module, "_SQL_COMPAT_GUARDS_INSTALLED", False)):
        return

    core_module._replace_qmark_placeholders = replace_qmark_placeholders
    core_module._translate_sqlite_master_tables_query = translate_sqlite_master_tables_query

    cursor_type = core_module.PostgresCompatCursor
    original_execute: Callable[..., Any] = cursor_type.execute
    original_executemany: Callable[..., Any] = cursor_type.executemany

    def guarded_execute(
        self: Any,
        sql: str,
        params: Sequence[Any] = (),
    ) -> Any:
        validate_sqlite_compat_statement(sql)
        return original_execute(self, sql, params)

    def guarded_executemany(
        self: Any,
        sql: str,
        seq_of_params: Any,
    ) -> Any:
        validate_sqlite_compat_statement(sql)
        return original_executemany(self, sql, seq_of_params)

    cursor_type.execute = guarded_execute
    cursor_type.executemany = guarded_executemany
    core_module._SQL_COMPAT_GUARDS_INSTALLED = True


__all__ = [
    "count_qmark_placeholders",
    "install_sql_compat_guards",
    "replace_qmark_placeholders",
    "rewrite_qmark_placeholders",
    "translate_sqlite_master_tables_query",
    "validate_sqlite_compat_statement",
]
