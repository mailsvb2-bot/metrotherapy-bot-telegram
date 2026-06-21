from __future__ import annotations

"""Detect unsafe exception handling patterns in Telegram handlers.

The handler layer may intentionally fail open for user-facing UX, but it should
not use broad `except Exception` or silently swallow failures with `pass` inside
`except` blocks. Optional ImportError feature probes are allowed to stay silent.
"""

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HANDLERS_DIR = ROOT / "handlers"
BROAD_EXCEPTION_NAMES = {"Exception", "BaseException"}
ALLOWED_SILENT_PASS_EXCEPTIONS = {"ImportError", "ModuleNotFoundError"}


def _exception_names(node: ast.AST | None) -> set[str]:
    if node is None:
        return {"<bare>"}
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    if isinstance(node, ast.Tuple):
        names: set[str] = set()
        for item in node.elts:
            names.update(_exception_names(item))
        return names
    return {type(node).__name__}


def _has_pass_statement(node: ast.ExceptHandler) -> bool:
    return any(isinstance(child, ast.Pass) for child in node.body)


def _scan_file(path: Path) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [
            {
                "path": str(path.relative_to(ROOT)),
                "line": int(exc.lineno or 0),
                "kind": "syntax_error",
                "exceptions": ["syntax_error"],
            }
        ]

    offenses: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        names = _exception_names(node.type)
        if "<bare>" in names or names.intersection(BROAD_EXCEPTION_NAMES):
            offenses.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "kind": "broad_except",
                    "exceptions": sorted(names),
                }
            )
        if _has_pass_statement(node) and not names.issubset(ALLOWED_SILENT_PASS_EXCEPTIONS):
            offenses.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "line": int(getattr(node, "lineno", 0) or 0),
                    "kind": "silent_except_pass",
                    "exceptions": sorted(names),
                }
            )
    return offenses


def run_audit() -> dict[str, Any]:
    files = sorted(p for p in HANDLERS_DIR.rglob("*.py") if p.is_file())
    offenses: list[dict[str, Any]] = []
    for path in files:
        offenses.extend(_scan_file(path))
    return {
        "ok": not offenses,
        "probe": "handler_exception_boundary_audit",
        "status": "GREEN" if not offenses else "RED",
        "files_scanned": len(files),
        "offense_count": len(offenses),
        "offenses": offenses[:100],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit handler broad exceptions and silent except-pass")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = run_audit()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"HANDLER_EXCEPTION_BOUNDARY_AUDIT_{payload['status']} offenses={payload['offense_count']} files={payload['files_scanned']}")
        for item in payload["offenses"]:
            print(f"{item['path']}:{item['line']} {item['kind']} {','.join(item['exceptions'])}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
