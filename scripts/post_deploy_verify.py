from __future__ import annotations

"""Post-deploy verification bundle for the Metrotherapy service.

This script is intentionally explicit and conservative. It combines the checks
that were previously run manually after deploy into one repeatable command:

- optional pytest run;
- production validator;
- smoke bootstrap;
- DB-backed scheduler/idempotency probe;
- auto-audio dry-run probe without Telegram sends;
- optional Postgres restore drill;
- local health/readiness HTTP probes.

It does not modify systemd units and does not send Telegram messages.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, env: Mapping[str, str] | None = None) -> str:
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(k): str(v) for k, v in env.items()})
    proc = subprocess.run(cmd, cwd=str(ROOT), env=merged_env, text=True, capture_output=True, check=False)
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise SystemExit(
            "POST_DEPLOY_VERIFY_FAILED command="
            + " ".join(cmd)
            + f" exit={proc.returncode}\n"
            + output.strip()
        )
    return output.strip()


def _decode_error_body(exc: urllib.error.HTTPError) -> str:
    body = exc.read().decode("utf-8", errors="replace")
    return body[:1000]


def _parse_json_body(*, url: str, body: str) -> dict:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED url={url} invalid_json={body[:300]}") from exc


def _http_json(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # nosec B310 - local operator-provided probe URL
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = _decode_error_body(exc)
        payload = _parse_json_body(url=url, body=body) if body.strip().startswith("{") else None
        if payload is not None:
            raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED url={url} status={exc.code} payload={payload}") from exc
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED url={url} status={exc.code} body={body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED url={url} err={exc}") from exc
    payload = _parse_json_body(url=url, body=body)
    if payload.get("ok") is not True:
        raise SystemExit(f"POST_DEPLOY_VERIFY_FAILED url={url} payload={payload}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repeatable post-deploy proof checks")
    parser.add_argument("--skip-pytest", action="store_true", help="Skip pytest for faster repeated local checks")
    parser.add_argument("--restore-drill", action="store_true", help="Run postgres_restore_drill.py --latest as part of the bundle")
    parser.add_argument("--health-url", default=os.getenv("HEALTH_URL", "http://127.0.0.1:8082/healthz"))
    parser.add_argument("--ready-url", default=os.getenv("READINESS_URL", "http://127.0.0.1:8082/readyz"))
    args = parser.parse_args()

    if not args.skip_pytest:
        print("==> pytest", flush=True)
        print(_run([sys.executable, "-m", "pytest", "-q"]))

    strict_env = {
        "APP_ENV": "prod",
        "VALIDATOR_RELEASE_MODE": "1",
        "VALIDATOR_GUARDRAILS_STRICT": "1",
    }

    print("==> prod validator", flush=True)
    print(_run([sys.executable, "scripts/validate_project.py"], env=strict_env))

    print("==> smoke", flush=True)
    print(_run([sys.executable, "scripts/smoke.py"], env=strict_env))

    print("==> scheduler job probe", flush=True)
    print(_run([sys.executable, "scripts/probe_scheduler_job_live.py"]))

    print("==> auto-audio dry-run probe", flush=True)
    print(_run([sys.executable, "scripts/probe_auto_audio_dry_run.py"]))

    if args.restore_drill:
        print("==> postgres restore drill", flush=True)
        print(_run([sys.executable, "scripts/postgres_restore_drill.py", "--latest"]))

    print("==> healthz", flush=True)
    health = _http_json(str(args.health_url))
    print(json.dumps({"ok": health.get("ok"), "probe": health.get("probe"), "db_engine": health.get("db_engine")}, ensure_ascii=False))

    print("==> readyz", flush=True)
    ready = _http_json(str(args.ready_url))
    print(
        json.dumps(
            {
                "ok": ready.get("ok"),
                "probe": ready.get("probe"),
                "db_ready": ready.get("db_ready"),
                "schema_ready": ready.get("schema_ready"),
                "scheduler_ready": ready.get("scheduler_ready"),
                "webhook_ready": ready.get("webhook_ready"),
            },
            ensure_ascii=False,
        )
    )

    print("POST_DEPLOY_VERIFY_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
