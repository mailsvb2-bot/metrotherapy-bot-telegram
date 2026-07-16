from http.server import BaseHTTPRequestHandler, HTTPServer
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
SYSTEMD_RUN = "/usr/bin/systemd-run"
_TRIGGER_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ZERO_SHA = "0" * 40


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
            body = (
                "ok: github deploy webhook accepts POST "
                f"local_branch_count={len(branches)} "
                f"local_branches={','.join(branches)}"
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
