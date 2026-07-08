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
# Reviewed: operator audit helper invokes local Bandit module with fixed arguments and no shell.
import subprocess  # nosec B404
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


@dataclass(frozen=True)
class BanditRun:
    returncode: int
    stdout: str
    stderr: str


def _run_bandit() -> BanditRun:
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
    # Reviewed: fixed Bandit inventory command, no shell, operator-only audit helper.
    proc = subprocess.run(  # nosec B603
        cmd,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return BanditRun(returncode=int(proc.returncode), stdout=proc.stdout or "", stderr=proc.stderr or "")


def _json_slice(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        return text
    return text[start : end + 1]


def _load_bandit_payload(run: BanditRun) -> dict:
    candidates = (run.stdout, run.stdout + "\n" + run.stderr)
    errors: list[str] = []
    for raw in candidates:
        candidate = _json_slice(raw)
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if isinstance(payload, dict):
            return payload
    diagnostics = "\n".join(errors)
    tail = ((run.stdout + "\n" + run.stderr).strip())[-2000:]
    raise SystemExit(f"BANDIT_SUBPROCESS_INVENTORY_FAILED: bandit_json_error={diagnostics}\n{tail}")


def collect_findings() -> list[Finding]:
    run = _run_bandit()
    payload = _load_bandit_payload(run)
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
