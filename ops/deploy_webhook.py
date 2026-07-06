from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.command_runner import DEVNULL, spawn_command

HOST = "127.0.0.1"
PORT = 9001
SECRET = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
DEPLOY_SH = "/root/metrotherapy/deploy.sh"
LOCK_FILE = Path("/tmp/metrotherapy_deploy.lock")
LOG_FILE = "/var/log/metrotherapy_deploy.log"


def _run_deploy_background():
    script = f"""
set -Eeuo pipefail
if [ -e {LOCK_FILE} ]; then
  echo "=== deploy skipped: lock exists $(date -Is) ===" >> {LOG_FILE}
  exit 0
fi
touch {LOCK_FILE}
trap 'rm -f {LOCK_FILE}' EXIT
echo "=== deploy queued started: $(date -Is) ===" >> {LOG_FILE}
{DEPLOY_SH} >> {LOG_FILE} 2>&1
echo "=== deploy queued finished: $(date -Is) ===" >> {LOG_FILE}
"""
    spawn_command(
        ["/usr/bin/bash", "-lc", script],
        stdout=DEVNULL,
        stderr=DEVNULL,
        start_new_session=True,
    )


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/github-deploy":
            self._send(200, b"ok: github deploy webhook accepts POST")
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
