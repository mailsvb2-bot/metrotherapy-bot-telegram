from __future__ import annotations

"""User Scenario Gate.

Hermetic acceptance gate for the critical user path. The default mode does not
load production env, does not call Telegram, does not call YooKassa, and uses a
private temporary SQLite database. Production mode reuses the same synthetic
journey probe against the configured deployment DB and still avoids provider
network calls and Telegram sends.
"""

import argparse
import json
import os
import shutil
import subprocess  # nosec B404 - fixed local probe command, no shell
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SYNTHETIC_USER_ID = -910_000_701
DEFAULT_PROD_ENV_FILE = "/etc/metrotherapy/metrotherapy.env"


@dataclass(frozen=True)
class UserScenarioGateResult:
    ok: bool
    mode: str
    user_id: int
    checks: dict[str, bool]
    probe: dict[str, Any]
    probe_returncode: int
    detail: str


def _smoke_bot_token() -> str:
    return "".join(("1234", "56789", ":", "ABCDE", "FGHIJ", "KLMNO", "PQRST", "UVWXY", "Zabcd", "efghi"))


def _tail(text: str, *, max_lines: int = 80) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-max_lines:])


def _last_json_object(text: str) -> dict[str, Any]:
    for raw_line in reversed((text or "").splitlines()):
        line = raw_line.strip()
        if not line.startswith("{") or not line.endswith("}"):
            continue
        loaded = json.loads(line)
        if isinstance(loaded, dict):
            return loaded
    raise ValueError("probe_json_not_found")


def _int(payload: dict[str, Any], key: str) -> int:
    try:
        return int(payload.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _hermetic_env(temp_db: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "LOAD_DOTENV": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "VALIDATOR_RELEASE_MODE": "1",
            "VALIDATOR_GUARDRAILS_STRICT": "1",
            "VALIDATOR_SKIP_AUDIO": "1",
            "METRO_DB_ENGINE": "sqlite",
            "METRO_DB_PATH": str(temp_db),
            "DATABASE_URL": "",
            "BOT_TOKEN": _smoke_bot_token(),
            "PAY_PROVIDER_TOKEN": "000000:SMOKE",
            "ADMIN_IDS": "1",
            "YOOKASSA_SHOP_ID": "scenario-shop",
            "YOOKASSA_SECRET_KEY": "scenario-secret",
            "PAYMENT_CHECKOUT_SIGNING_KEY": "scenario-checkout-signing-key",
            "YOOKASSA_WEBHOOK_SECRET": "scenario-webhook-secret",
            "PAYMENT_PUBLIC_BASE_URL": "https://metrotherapy.example",
            "TELEGRAM_TRANSPORT": "polling",
            "TELEGRAM_WEBHOOK_ENABLED": "0",
            "TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED": "0",
            "MESSENGER_WEBHOOK_ENABLED": "0",
        }
    )
    return env


def _prod_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("APP_ENV", "prod")
    env.setdefault("LOAD_DOTENV", "0")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def _build_checks(payload: dict[str, Any], *, returncode: int, keep_artifacts: bool) -> dict[str, bool]:
    cleanup_status = str(payload.get("cleanup_status", ""))
    checks = {
        "probe_exit_zero": returncode == 0,
        "probe_ok": payload.get("ok") is True,
        "no_problems": not payload.get("problems"),
        "cleanup_clean_or_kept": cleanup_status in ({"clean", "kept"} if keep_artifacts else {"clean"}),
        "demo_ack_ok": payload.get("demo_ack_ok") is True,
        "payment_granted_tokens": _int(payload, "wallet_delta_after_payment") > 0,
        "payment_created_entitlement": _int(payload, "entitlement_rows_delta") > 0,
        "payment_created_outbox": _int(payload, "outbox_rows_delta") > 0,
        "payment_created_consultation": _int(payload, "consultation_rows_delta") > 0,
        "paid_audio_consumed_token": _int(payload, "used_tokens_after_paid_audio") >= 1,
        "rows_touched_recorded": _int(payload, "rows_touched") > 0,
    }
    return checks


def run_gate(*, mode: str, env_file: str, user_id: int, keep_artifacts: bool, timeout_sec: int) -> UserScenarioGateResult:
    temp_dir = Path(tempfile.mkdtemp(prefix="metro_user_scenario_gate_"))
    temp_db = temp_dir / "scenario.db"
    try:
        if mode == "prod":
            probe_env = _prod_env()
            probe_env_file = env_file or DEFAULT_PROD_ENV_FILE
        else:
            probe_env = _hermetic_env(temp_db)
            probe_env_file = ""

        cmd = [
            sys.executable,
            "scripts/probe_user_journey_e2e.py",
            "--env-file",
            probe_env_file,
            "--user-id",
            str(int(user_id)),
            "--json",
        ]
        if keep_artifacts:
            cmd.append("--keep-artifacts")

        proc = subprocess.run(  # nosec B603 - fixed local probe command, no shell
            cmd,
            cwd=ROOT,
            env=probe_env,
            check=False,
            capture_output=True,
            text=True,
            timeout=int(timeout_sec),
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        try:
            payload = _last_json_object(output)
        except (json.JSONDecodeError, ValueError) as exc:
            detail = f"probe_json_error:{type(exc).__name__}:{exc}\n{_tail(output)}"
            checks = {"probe_json_found": False, "probe_exit_zero": proc.returncode == 0}
            return UserScenarioGateResult(
                ok=False,
                mode=mode,
                user_id=int(user_id),
                checks=checks,
                probe={},
                probe_returncode=int(proc.returncode),
                detail=detail,
            )

        checks = _build_checks(payload, returncode=int(proc.returncode), keep_artifacts=keep_artifacts)
        ok = all(checks.values())
        failed = [name for name, passed in checks.items() if not passed]
        detail = "ok" if ok else f"failed_checks={failed}; probe_tail={_tail(output)}"
        return UserScenarioGateResult(
            ok=ok,
            mode=mode,
            user_id=int(user_id),
            checks=checks,
            probe=payload,
            probe_returncode=int(proc.returncode),
            detail=detail,
        )
    except subprocess.TimeoutExpired as exc:
        return UserScenarioGateResult(
            ok=False,
            mode=mode,
            user_id=int(user_id),
            checks={"probe_timeout": False},
            probe={},
            probe_returncode=124,
            detail=f"TimeoutExpired:{exc}",
        )
    finally:
        if not keep_artifacts or mode != "hermetic":
            shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run hermetic critical user scenario acceptance gate")
    parser.add_argument("--mode", choices=("hermetic", "prod"), default="hermetic")
    parser.add_argument("--env-file", default=DEFAULT_PROD_ENV_FILE)
    parser.add_argument("--user-id", type=int, default=DEFAULT_SYNTHETIC_USER_ID)
    parser.add_argument("--keep-artifacts", action="store_true")
    parser.add_argument("--timeout-sec", type=int, default=120)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_gate(
        mode=str(args.mode),
        env_file=str(args.env_file),
        user_id=int(args.user_id),
        keep_artifacts=bool(args.keep_artifacts),
        timeout_sec=int(args.timeout_sec),
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif result.ok:
        probe = result.probe
        print(
            "USER_SCENARIO_GATE_OK "
            f"mode={result.mode} user_id={result.user_id} "
            f"cleanup={probe.get('cleanup_status')} rows_touched={probe.get('rows_touched')} "
            f"wallet_delta={probe.get('wallet_delta_after_payment')} "
            f"used_tokens={probe.get('used_tokens_after_paid_audio')}"
        )
    else:
        print("USER_SCENARIO_GATE_FAILED")
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
