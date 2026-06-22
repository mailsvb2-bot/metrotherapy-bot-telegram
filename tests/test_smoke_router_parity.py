from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _included_router_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "include_router":
            continue
        if not node.args:
            continue

        arg = node.args[0]
        if not isinstance(arg, ast.Attribute) or arg.attr != "router":
            continue
        owner = arg.value
        if isinstance(owner, ast.Name):
            names.append(owner.id)

    return names


def test_smoke_router_list_matches_app_runtime() -> None:
    """The smoke gate must exercise the same routers as production startup.

    This test intentionally uses AST instead of importing app.py. Importing the
    application can touch optional runtime dependencies and environment-backed
    settings; the parity contract itself is static and should stay lightweight.
    """

    app_routers = _included_router_names(ROOT / "app.py")
    smoke_routers = _included_router_names(ROOT / "scripts" / "smoke.py")

    assert smoke_routers == app_routers
