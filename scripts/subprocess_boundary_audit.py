from __future__ import annotations

"""Ensure subprocess usage stays behind the audited command runner boundary."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_FILES = {
    Path("services/command_runner.py"),
}
SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "build",
    "dist",
    "data",
    "audio",
    "logs",
    "tmp",
    "tests",
}


def _skip(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    return any(part in SKIP_PARTS for part in rel.parts)


def _is_subprocess_attr(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "subprocess"
    )


def _violations(path: Path) -> list[str]:
    rel = path.relative_to(ROOT)
    if rel in ALLOWED_FILES:
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
    except SyntaxError as exc:
        return [f"{rel}:{exc.lineno}: syntax error while auditing subprocess boundary"]

    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    problems.append(f"{rel}:{node.lineno}: forbidden import subprocess")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                problems.append(f"{rel}:{node.lineno}: forbidden from subprocess import ...")
        elif isinstance(node, ast.Call) and _is_subprocess_attr(node.func):
            attr = node.func.attr
            problems.append(f"{rel}:{node.lineno}: forbidden subprocess.{attr} call")
    return problems


def main() -> int:
    problems: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        if _skip(path):
            continue
        problems.extend(_violations(path))

    if problems:
        print("SUBPROCESS_BOUNDARY_AUDIT_FAILED")
        for problem in problems:
            print(problem)
        return 1

    print("SUBPROCESS_BOUNDARY_AUDIT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
