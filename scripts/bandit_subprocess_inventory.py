from __future__ import annotations

"""Inventory Bandit B404/B603 findings without auto-fixing them.

This is an intentionally read-only helper for the remaining subprocess/import
subprocess hardening work. It creates a stable, reviewable list so each finding
can be classified and fixed or documented one by one instead of being silenced
globally.
"""

import argparse
import json
import re
import subprocess  # nosec B404 - this script invokes Bandit itself as an operator audit command
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_ISSUES = {"B404", "B603"}
DEFAULT_EXCLUDES = (".venv", "venv", ".git", "data", "audio", "logs", "build", "dist")


@dataclass(frozen=True)
class Finding:
    issue: str
    severity: str
    confidence: str
    path: str
    line: int
    text: str


def _run_bandit() -> tuple[int, str]:
    cmd = [
        sys.executable,
        "-m",
        "bandit",
        "-r",
        ".",
        "-c",
        "pyproject.toml",
        "-x",
        ",".join(f"./{item}" for item in DEFAULT_EXCLUDES),
        "-f",
        "json",
    ]
    proc = subprocess.run(  # nosec B603 - fixed Bandit command, no shell, operator-only audit helper
        cmd,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return int(proc.returncode), (proc.stdout or "") + (proc.stderr or "")


def _load_bandit_payload(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"BANDIT_SUBPROCESS_INVENTORY_FAILED: bandit_json_error={exc}\n{raw[-2000:]}") from exc


def collect_findings() -> list[Finding]:
    _code, raw = _run_bandit()
    payload = _load_bandit_payload(raw)
    findings: list[Finding] = []
    for item in payload.get("results") or []:
        issue = str(item.get("test_id") or "")
        if issue not in TARGET_ISSUES:
            continue
        filename = str(item.get("filename") or "")
        path = str(Path(filename).relative_to(ROOT)) if filename.startswith(str(ROOT)) else filename
        text = re.sub(r"\s+", " ", str(item.get("issue_text") or "")).strip()
        findings.append(
            Finding(
                issue=issue,
                severity=str(item.get("issue_severity") or ""),
                confidence=str(item.get("issue_confidence") or ""),
                path=path,
                line=int(item.get("line_number") or 0),
                text=text,
            )
        )
    return sorted(findings, key=lambda item: (item.issue, item.path, item.line, item.text))


def _markdown(findings: list[Finding]) -> str:
    by_issue = {issue: [item for item in findings if item.issue == issue] for issue in sorted(TARGET_ISSUES)}
    lines = ["# Bandit subprocess inventory", "", "Read-only B404/B603 inventory. Do not bulk-silence these findings.", ""]
    for issue, items in by_issue.items():
        lines.append(f"## {issue} ({len(items)})")
        lines.append("")
        for item in items:
            lines.append(f"- `{item.path}:{item.line}` — {item.severity}/{item.confidence}: {item.text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory Bandit B404/B603 findings without changing code")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--markdown", action="store_true", help="Print Markdown report")
    parser.add_argument("--fail-if-any", action="store_true", help="Exit 2 when any B404/B603 findings are present")
    args = parser.parse_args()

    findings = collect_findings()
    if args.json:
        print(json.dumps([asdict(item) for item in findings], ensure_ascii=False, indent=2, sort_keys=True))
    elif args.markdown:
        print(_markdown(findings), end="")
    else:
        counts = {issue: sum(1 for item in findings if item.issue == issue) for issue in sorted(TARGET_ISSUES)}
        print(
            "BANDIT_SUBPROCESS_INVENTORY "
            + " ".join(f"{issue}={count}" for issue, count in counts.items())
            + f" total={len(findings)}"
        )
    return 2 if args.fail_if_any and findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
