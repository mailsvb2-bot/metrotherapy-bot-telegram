from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

BANNED_FUNCS = {
    "choose_offer", "pick_offer", "recommend_price", "select_price", "choose_price", "decide_price", "decide_offer"
}

# Modules where local decision logic often lives; importing them from handlers is a red flag.
BANNED_MODULE_PREFIXES = (
    "services.ai.pricing",
    "services.ai.decisions",
    "services.pricing",
    "services.pricing_update",
    "services.pricing_sync",
)

ALLOWED_FILES = {
    str(Path("core") / "ai" / "decision_core.py"),
    str(Path("tools") / "verify_sovereignty.py"),
}

def is_arch_violation_body(fn: ast.FunctionDef) -> bool:
    # Accept body that is just: arch_violation(...)
    if len(fn.body) != 1:
        return False
    node = fn.body[0]
    if not isinstance(node, ast.Expr):
        return False
    call = node.value
    if not isinstance(call, ast.Call):
        return False
    if isinstance(call.func, ast.Name) and call.func.id in {"arch_violation"}:
        return True
    if isinstance(call.func, ast.Attribute) and call.func.attr in {"arch_violation"}:
        return True
    return False

def _iter_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node

def _call_name(call: ast.Call) -> str | None:
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None

def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    bad_defs = []
    bad_calls = []
    bad_imports = []

    for py in repo.rglob("*.py"):
        rel = py.relative_to(repo).as_posix()
        if rel.startswith("."):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except OSError:
            continue
        except UnicodeDecodeError:
            continue
        except (SyntaxError, ValueError):
            continue

        # 1) Banned function definitions outside DecisionCore unless they are arch_violation stubs
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in BANNED_FUNCS:
                if rel in ALLOWED_FILES:
                    continue
                if not is_arch_violation_body(node):
                    bad_defs.append(f"{rel}:{node.lineno}:{node.name}")

        # 2) Direct calls to banned decision functions (anywhere)
        for call in _iter_calls(tree):
            name = _call_name(call)
            if name in BANNED_FUNCS:
                bad_calls.append(f"{rel}:{getattr(call, 'lineno', '?')}:{name}")

        # 3) Suspicious imports of decision modules from handlers/interface layer
        if rel.startswith("handlers/") or rel.startswith("keyboards/") or rel.startswith("core/middlewares"):
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if any(a.name.startswith(p) for p in BANNED_MODULE_PREFIXES):
                            bad_imports.append(f"{rel}:{node.lineno}:import {a.name}")
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    if any(mod.startswith(p) for p in BANNED_MODULE_PREFIXES):
                        bad_imports.append(f"{rel}:{node.lineno}:from {mod} import ...")

    if bad_defs:
        print("SOVEREIGNTY VIOLATION: banned decision function definitions outside DecisionCore:")
        for x in bad_defs:
            print(" -", x)
        return 2
    if bad_calls:
        print("SOVEREIGNTY VIOLATION: banned decision function calls found:")
        for x in bad_calls[:200]:
            print(" -", x)
        if len(bad_calls) > 200:
            print(f" ... ({len(bad_calls)-200} more)")
        return 3
    if bad_imports:
        print("SOVEREIGNTY WARNING: suspicious imports from interface layer:")
        for x in bad_imports[:200]:
            print(" -", x)
        return 4

    print("OK: sovereignty verify passed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
