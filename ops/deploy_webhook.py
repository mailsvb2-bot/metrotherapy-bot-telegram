from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
# Reviewed: operator-only webhook runner; commands use fixed absolute paths.
import subprocess  # nosec B404
from pathlib import Path
import uuid

HOST = "127.0.0.1"
PORT = 9001
SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
APP_DIR = Path("/root/metrotherapy")
DEPLOY_WORKER = APP_DIR / "scripts/run_deploy_worker.sh"
DEPLOY_LOG = Path("/var/log/metrotherapy_deploy.log")
SYSTEMD_RUN = "/usr/bin/systemd-run"
SYSTEMCTL = "/usr/bin/systemctl"
_TRIGGER_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHORT_TRIGGER_RE = re.compile(r"^[0-9a-f]{12}$")
_DEPLOY_UNIT_RE = re.compile(r"^metrotherapy-deploy-[0-9a-f]{12}\.service$")
_ZERO_SHA = "0" * 40
_LOG_TAIL_BYTES = 512 * 1024

_TRIGGER_LINE_RE = re.compile(r"^=== deploy trigger sha: ([0-9a-f]{40}) ===$")
_DEPLOY_STARTED_RE = re.compile(
    r"^=== deploy queued started trigger=([0-9a-f]{40}):"
)
_DEPLOY_FINISHED_RE = re.compile(
    r"^=== deploy queued finished trigger=([0-9a-f]{40}):"
)
_RESULT_LINE_RE = re.compile(
    r"^=== \[(?:max-trust-install|stars-provider-audit|max-provider-audit|vk-provider-audit)-result\] "
    r"trigger=([0-9a-f]{12})\b"
)
_RESULT_SKIP_RE = re.compile(
    r"^=== deploy skipped after published provider result trigger=([0-9a-f]{40}):"
)
_TRIGGER_UNAVAILABLE_RE = re.compile(
    r"^ERROR: deploy trigger commit is unavailable: ([0-9a-f]{40})$"
)
_RESULT_PUBLISH_ERROR_RE = re.compile(
    r"^ERROR: unable to publish audit result after retries: "
    r"\[(?:max-trust-install|stars-provider-audit|max-provider-audit|vk-provider-audit)-result\] "
    r"trigger=([0-9a-f]{12})\b"
)


class DeployQueueError(RuntimeError):
    """The authenticated deploy request could not be handed to systemd."""


def _validated_trigger_sha(value: object) -> str | None:
    """Return one immutable GitHub push SHA or reject an unusable event."""

    if not isinstance(value, str):
        return None
    trigger_sha = value.strip().lower()
    if trigger_sha == _ZERO_SHA or _TRIGGER_SHA_RE.fullmatch(trigger_sha) is None:
        return None
    return trigger_sha


def _run_deploy_background(trigger_sha: str) -> None:
    """Queue a deploy outside the webhook service cgroup.

    A deploy is allowed to restart ``github-deploy-webhook.service`` while it
    updates the webhook runtime. Running the deploy as a child of that service
    would make systemd kill the deploy together with the service restart. A
    transient service gives the worker an independent lifecycle and guarantees
    that its EXIT trap can remove the deploy lock.

    The triggering commit SHA is attached to the transient unit so queued
    workers never infer their purpose from a newer shared checkout.
    """

    validated_sha = _validated_trigger_sha(trigger_sha)
    if validated_sha is None:
        raise DeployQueueError("invalid deploy trigger sha")

    unit_name = f"metrotherapy-deploy-{uuid.uuid4().hex[:12]}"
    command = [
        SYSTEMD_RUN,
        "--unit",
        unit_name,
        "--collect",
        "--no-block",
        "--property=Type=exec",
        f"--property=WorkingDirectory={APP_DIR}",
        f"--setenv=DEPLOY_TRIGGER_SHA={validated_sha}",
        "/usr/bin/bash",
        str(DEPLOY_WORKER),
    ]
    try:
        completed = subprocess.run(  # nosec B603
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError as exc:
        raise DeployQueueError(f"systemd-run unavailable: {exc}") from exc
    except subprocess.SubprocessError as exc:
        raise DeployQueueError(f"systemd-run execution failed: {exc}") from exc

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "systemd-run failed").strip()
        raise DeployQueueError(detail[:500])


def _local_branch_topology() -> tuple[list[str], str | None]:
    try:
        completed = subprocess.run(  # nosec B603
            [
                "/usr/bin/git",
                "-C",
                str(APP_DIR),
                "for-each-ref",
                "--format=%(refname:short)",
                "refs/heads",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], f"{type(exc).__name__}:{exc}"

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git branch audit failed").strip()
        return [], detail[:240]
    branches = sorted(
        line.strip()
        for line in completed.stdout.splitlines()
        if line.strip()
    )
    return branches, None


def _deploy_unit_counts() -> tuple[int | None, int | None]:
    """Return only aggregate transient-worker counts, never unit environments."""

    try:
        completed = subprocess.run(  # nosec B603
            [
                SYSTEMCTL,
                "list-units",
                "--all",
                "--type=service",
                "--no-legend",
                "--plain",
                "metrotherapy-deploy-*.service",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None

    if completed.returncode != 0:
        return None, None

    total = 0
    running = 0
    for line in completed.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or _DEPLOY_UNIT_RE.fullmatch(parts[0]) is None:
            continue
        total += 1
        if parts[2] in {"active", "activating", "reloading"}:
            running += 1
    return total, running


def _read_log_tail(path: Path) -> tuple[list[str], str]:
    """Read a bounded log tail and return a safe timestamp for observability."""

    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - _LOG_TAIL_BYTES), os.SEEK_SET)
            payload = stream.read(_LOG_TAIL_BYTES)
        updated_at = datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        return [], "unknown"
    return payload.decode("utf-8", errors="ignore").splitlines(), updated_at


def _safe_deploy_log_status(path: Path = DEPLOY_LOG) -> dict[str, str]:
    """Derive allowlisted worker state from fixed-format log markers only.

    Free-form log text is never returned. This deliberately exposes no command
    output, provider response, token, secret, confirmation code, or environment.
    """

    lines, updated_at = _read_log_tail(path)
    trigger = "unknown"
    stage = "unknown"
    code = "unknown"

    for raw_line in lines:
        line = raw_line.strip()
        match = _TRIGGER_LINE_RE.fullmatch(line)
        if match:
            trigger = match.group(1)
            stage = "trigger_loaded"
            code = "0"
            continue

        match = _DEPLOY_STARTED_RE.match(line)
        if match:
            trigger = match.group(1)
            stage = "deploying"
            code = "0"
            continue

        match = _DEPLOY_FINISHED_RE.match(line)
        if match:
            trigger = match.group(1)
            stage = "post_deploy_audit"
            code = "0"
            continue

        match = _RESULT_SKIP_RE.match(line)
        if match:
            trigger = match.group(1)
            stage = "result_trigger_skipped"
            code = "0"
            continue

        match = _RESULT_LINE_RE.match(line)
        if match:
            prefix = match.group(1)
            trigger = trigger if trigger.startswith(prefix) else prefix
            stage = "result_published"
            code = "0"
            continue

        match = _TRIGGER_UNAVAILABLE_RE.fullmatch(line)
        if match:
            trigger = match.group(1)
            stage = "trigger_unavailable"
            code = "36"
            continue

        match = _RESULT_PUBLISH_ERROR_RE.match(line)
        if match:
            prefix = match.group(1)
            trigger = trigger if trigger.startswith(prefix) else prefix
            stage = "result_publish_error"
            code = "34"
            continue

        if (
            line.startswith("=== production env migrations rolled back after failed deploy:")
            and trigger != "unknown"
        ):
            stage = "deploy_failed"
            code = "1"

    if not (_TRIGGER_SHA_RE.fullmatch(trigger) or _SHORT_TRIGGER_RE.fullmatch(trigger)):
        trigger = "unknown"
    return {
        "trigger": trigger[:12] if trigger != "unknown" else trigger,
        "stage": stage,
        "code": code,
        "updated_at": updated_at,
    }


def _deploy_observability() -> dict[str, str]:
    total, running = _deploy_unit_counts()
    status = _safe_deploy_log_status()
    status["units"] = str(total) if total is not None else "unknown"
    status["running"] = str(running) if running is not None else "unknown"

    if running == 0 and status["stage"] == "deploying":
        status["stage"] = "deploy_exited_before_finish"
        if status["code"] == "0":
            status["code"] = "unknown"
    elif running == 0 and status["stage"] == "post_deploy_audit":
        status["stage"] = "post_deploy_audit_exited"
        if status["code"] == "0":
            status["code"] = "unknown"
    return status


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/github-deploy":
            branches, error = _local_branch_topology()
            if error is not None:
                self._send(503, f"error: branch audit unavailable: {error}".encode("utf-8"))
                return
            deploy = _deploy_observability()
            body = (
                "ok: github deploy webhook accepts POST "
                f"local_branch_count={len(branches)} "
                f"local_branches={','.join(branches)} "
                f"deploy_units={deploy['units']} "
                f"deploy_running={deploy['running']} "
                f"deploy_last_trigger={deploy['trigger']} "
                f"deploy_last_stage={deploy['stage']} "
                f"deploy_last_code={deploy['code']} "
                f"deploy_log_updated_at={deploy['updated_at']}"
            )
            self._send(200, body.encode("utf-8"))
        else:
            self._send(404, b"not found")

    def do_POST(self):
        if self.path != "/github-deploy":
            self._send(404, b"not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        signature = self.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            SECRET.encode("utf-8"), body, hashlib.sha256
        ).hexdigest()

        if not SECRET or not hmac.compare_digest(signature, expected):
            self._send(403, b"bad signature")
            return
        event = self.headers.get("X-GitHub-Event", "")
        if event == "ping":
            self._send(200, b"pong")
            return

        if event != "push":
            self._send(202, b"ignored")
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send(400, b"bad json")
            return

        if payload.get("ref") != "refs/heads/main":
            self._send(202, b"not main")
            return

        trigger_sha = _validated_trigger_sha(payload.get("after"))
        if trigger_sha is None:
            self._send(400, b"bad after sha")
            return

        try:
            _run_deploy_background(trigger_sha)
        except DeployQueueError as exc:
            self._send(503, f"deploy queue failed: {exc}".encode("utf-8"))
            return
        self._send(202, b"deploy queued")

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    HTTPServer((HOST, PORT), Handler).serve_forever()
