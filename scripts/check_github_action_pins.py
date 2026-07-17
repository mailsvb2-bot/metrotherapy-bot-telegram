from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
IMMUTABLE_REF_RE = re.compile(r"^[0-9a-f]{40}$")


def mutable_action_references(workflows_dir: Path = WORKFLOWS_DIR) -> list[str]:
    problems: list[str] = []
    if not workflows_dir.exists():
        return [f"workflow directory missing: {workflows_dir}"]

    for path in sorted((*workflows_dir.glob("*.yml"), *workflows_dir.glob("*.yaml"))):
        text = path.read_text(encoding="utf-8")
        for match in USES_RE.finditer(text):
            reference = match.group(1).strip()
            if reference.startswith(("./", "docker://")):
                continue
            if "@" not in reference:
                problems.append(f"{path.relative_to(ROOT)}: missing ref: {reference}")
                continue
            action, ref = reference.rsplit("@", 1)
            if not action or not IMMUTABLE_REF_RE.fullmatch(ref):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(
                    f"{path.relative_to(ROOT)}:{line}: external action must use a 40-char commit SHA: {reference}"
                )
    return problems


def main() -> int:
    problems = mutable_action_references()
    if problems:
        print("GITHUB_ACTION_PIN_GATE_FAILED")
        for problem in problems:
            print(problem)
        return 1
    print("GITHUB_ACTION_PIN_GATE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
