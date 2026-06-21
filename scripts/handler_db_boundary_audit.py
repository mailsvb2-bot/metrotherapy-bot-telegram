from __future__ import annotations

"""Detect direct low-level DB work inside async Telegram handlers.

Handlers may call synchronous storage code through asyncio.to_thread helpers, but
low-level DB calls inside an async handler body can block the event loop. This
script is a regression guard for that boundary.
"""

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HANDLERS_DIR = ROOT / "handlers"

LOW_LEVEL_CALL_NAMES = {"db", "get_connection"}
LOW_LEVEL_ATTR_NAMES = {"execute", "executemany", "executescript", "commit", "rollback"}
SQL_METHOD_RECEIVER_NAMES = {"conn", "connection", "cur", "cursor"}
ALLOWED_ASYNC_WRAPPERS = {"to_thread", "run_in_executor", "_to_thread"}


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _receiver_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute):
        value = node.value
        if isinstance(value, ast.Name):
            return value.id
    return ""


def _is_allowed_wrapper_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in ALLOWED_ASYNC_WRAPPERS
    if isinstance(func, ast.Attribute):
        return func.attr in ALLOWED_ASYNC_WRAPPERS
    return False


def _is_low_level_db_call(node: ast.Call) -> bool:
    func = node.func
    name = _call_name(func)
    if name in LOW_LEVEL_CALL_NAMES:
        return True
    if isinstance(func, ast.Attribute) and name in LOW_LEVEL_ATTR_NAMES:
        receiver = _receiver_name(func)
        if receiver in SQL_METHOD_RECEIVER_NAMES:
            return True
    return False


class _AsyncBodyScanner(ast.NodeVisitor):
    def __init__(self, path: Path, async_name: str) -> None:
        self.path = path
        self.async_name = async_name
        self.offenses: list[dict[str, Any]] = []
        self._allowed_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # nested sync helper is allowed
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # nested async helper has own scan
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        is_wrapper = _is_allowed_wrapper_call(node)
        if is_wrapper:
            self._allowed_depth += 1
        try:
            if self._allowed_depth == 0 and _is_low_level_db_call(node):
                self.offenses.append(
                    {
                        "path": str(self.path.relative_to(ROOT)),
                        "async_function": self.async_name,
                        "line": int(getattr(node, "lineno", 0) or 0),
                        "call": _call_name(node.func),
                    }
                )
            self.generic_visit(node)
        finally:
            if is_wrapper:
                self._allowed_depth -= 1


class _ModuleScanner(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offenses: list[dict[str, Any]] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        scanner = _AsyncBodyScanner(self.path, node.name)
        for item in node.body:
            scanner.visit(item)
        self.offenses.extend(scanner.offenses)
        for item in node.body:
            if isinstance(item, ast.AsyncFunctionDef):
                self.visit_AsyncFunctionDef(item)


def _scan_file(path: Path) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [
            {
                "path": str(path.relative_to(ROOT)),
                "async_function": "<parse>",
                "line": int(exc.lineno or 0),
                "call": "syntax_error",
            }
        ]
    scanner = _ModuleScanner(path)
    scanner.visit(tree)
    return scanner.offenses


def run_audit() -> dict[str, Any]:
    files = sorted(p for p in HANDLERS_DIR.rglob("*.py") if p.is_file())
    offenses: list[dict[str, Any]] = []
    for path in files:
        offenses.extend(_scan_file(path))
    return {
        "ok": not offenses,
        "probe": "handler_db_boundary_audit",
        "status": "GREEN" if not offenses else "RED",
        "files_scanned": len(files),
        "offense_count": len(offenses),
        "offenses": offenses[:50],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit direct DB/SQL calls inside async handlers")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = run_audit()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"HANDLER_DB_BOUNDARY_AUDIT_{payload['status']} offenses={payload['offense_count']} files={payload['files_scanned']}")
        for item in payload["offenses"]:
            print(f"{item['path']}:{item['line']} {item['async_function']} direct {item['call']}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
