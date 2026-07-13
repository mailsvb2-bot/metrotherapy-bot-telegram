from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import hmac
import json
import os
# Reviewed: operator-only webhook runner; command body is built from fixed paths.
import subprocess  # nosec B404
from pathlib import Path

HOST = "127.0.0.1"
PORT = 9001
SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
APP_DIR = Path("/root/metrotherapy")
DEPLOY_SH = str(APP_DIR / "deploy.sh")
LOCK_FILE = APP_DIR / "data/deploy/metrotherapy_deploy.lock"
LOG_FILE = "/var/log/metrotherapy_deploy.log"


def _run_deploy_background():
    script = f"""
set -Eeuo pipefail
mkdir -p {LOCK_FILE.parent}
if [ -e {LOCK_FILE} ]; then
  echo "=== deploy skipped: lock exists $(date -Is) ===" >> {LOG_FILE}
  exit 0
fi
touch {LOCK_FILE}
trap 'rm -f {LOCK_FILE}' EXIT
echo "=== deploy queued started: $(date -Is) ===" >> {LOG_FILE}
/usr/bin/bash {DEPLOY_SH} >> {LOG_FILE} 2>&1
echo "=== deploy queued finished: $(date -Is) ===" >> {LOG_FILE}
"""
    # Reviewed: the webhook verifies GitHub HMAC and runs a fixed local deploy script.
    subprocess.Popen(  # nosec B603
        ["/usr/bin/bash", "-lc", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


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

        _run_deploy_background()
        self._send(202, b"deploy queued")

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    HTTPServer((HOST, PORT), Handler).serve_forever()
