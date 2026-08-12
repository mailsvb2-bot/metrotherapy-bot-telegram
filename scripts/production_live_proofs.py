from __future__ import annotations

"""Explicit, trigger-bound production live proofs.

This operator script is intentionally inert unless the immutable deploy worker
passes a Git commit carrying one of the allowlisted request markers.  It never
prints provider credentials or environment values.  The two supported actions
are:

* repair a stale VK Callback API confirmation code from the official VK API,
  atomically restart the service, and require the normal provider audit to pass;
* exercise the immutable ``current``/``previous`` rollback path on the real
  server, prove both sealed release hashes survive byte-for-byte, and restore
  the original release before returning success.
"""

import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

APP_DIR = Path(os.getenv("APP_DIR", "/root/metrotherapy"))
ENV_FILE = Path(os.getenv("ENV_FILE", "/etc/metrotherapy/metrotherapy.env"))
LOCK_FILE = Path(
    os.getenv(
        "LOCK_FILE",
        str(APP_DIR / "data" / "deploy" / "metrotherapy_deploy.lock"),
    )
)
RUNTIME_ROOT = Path(os.getenv("METRO_RUNTIME_ROOT", "/var/lib/metrotherapy/runtime"))
CURRENT_LINK = Path(
    os.getenv("METRO_CURRENT_RELEASE_LINK", str(RUNTIME_ROOT / "current"))
)
PREVIOUS_LINK = Path(
    os.getenv("METRO_PREVIOUS_RELEASE_LINK", str(RUNTIME_ROOT / "previous"))
)
DEPLOY_STATE_DIR = Path(os.getenv("DEPLOY_STATE_DIR", "/var/lib/metrotherapy/deploy-state"))
DEPLOYMENT_PROOF_FILE = Path(
    os.getenv("DEPLOYMENT_PROOF_FILE", str(DEPLOY_STATE_DIR / "deployment-proof.json"))
)
DEPLOYED_SHA_FILE = Path(
    os.getenv("DEPLOYED_SHA_FILE", str(DEPLOY_STATE_DIR / "deployed_sha"))
)
SERVICE_NAME = os.getenv("SERVICE_NAME", "metrotherapy.service")
LOCAL_HEALTH_URL = os.getenv("LOCAL_HEALTH_URL", "http://127.0.0.1:8082/healthz")
LOCAL_READY_URL = os.getenv("LOCAL_READY_URL", "http://127.0.0.1:8082/readyz")
PUBLIC_HEALTH_URL = os.getenv(
    "PUBLIC_HEALTH_URL", "https://metrotherapy-bot.metrotherapy.ru/healthz"
)
IMMUTABLE_RELEASE = APP_DIR / "scripts" / "immutable_release.py"
WRITE_GUARD = APP_DIR / "scripts" / "install_runtime_write_guard.sh"
VK_AUDIT = APP_DIR / "scripts" / "vk_provider_audit.py"
TRIGGER_SHA = (os.getenv("DEPLOY_TRIGGER_SHA") or "").strip().lower()

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_RESULT_RE = re.compile(r"[^A-Za-z0-9_.:=,/-]+")
VK_API_BASE = "https://api.vk.com/method"


class LiveProofError(RuntimeError):
    pass


def _run(
    args: list[str],
    *,
    check: bool = True,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "command failed").strip()
        raise LiveProofError(f"command_failed:{Path(args[0]).name}:{completed.returncode}:{detail[:180]}")
    return completed


def _trigger_message() -> str:
    if _SHA_RE.fullmatch(TRIGGER_SHA) is None:
        raise LiveProofError("invalid_trigger_sha")
    _run(["/usr/bin/git", "-C", str(APP_DIR), "fetch", "origin", "main"], timeout=180)
    result = _run(
        ["/usr/bin/git", "-C", str(APP_DIR), "show", "-s", "--format=%B", TRIGGER_SHA],
        timeout=30,
    )
    return result.stdout


def _safe_fragment(value: object, *, limit: int = 180) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = _SAFE_RESULT_RE.sub("_", text)
    return text[:limit] or "NONE"


def _publish_result(message: str) -> None:
    safe_message = _safe_fragment(message, limit=500)
    for attempt in range(1, 4):
        _run(["/usr/bin/git", "-C", str(APP_DIR), "fetch", "origin", "main"], timeout=180)
        parent = _run(
            ["/usr/bin/git", "-C", str(APP_DIR), "rev-parse", "origin/main"], timeout=20
        ).stdout.strip()
        tree = _run(
            ["/usr/bin/git", "-C", str(APP_DIR), "rev-parse", f"{parent}^{{tree}}"], timeout=20
        ).stdout.strip()
        commit = subprocess.run(
            [
                "/usr/bin/git",
                "-C",
                str(APP_DIR),
                "-c",
                "user.name=Metrotherapy Live Proof",
                "-c",
                "user.email=live-proof@metrotherapy.local",
                "commit-tree",
                tree,
                "-p",
                parent,
                "-F",
                "-",
            ],
            input=safe_message + "\n",
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if commit.returncode != 0:
            raise LiveProofError("result_commit_failed")
        result_sha = commit.stdout.strip()
        pushed = _run(
            [
                "/usr/bin/git",
                "-C",
                str(APP_DIR),
                "push",
                "origin",
                f"{result_sha}:refs/heads/main",
            ],
            check=False,
            timeout=180,
        )
        if pushed.returncode == 0:
            return
        time.sleep(attempt)
    raise LiveProofError("result_publish_race")


def _load_selected_env(names: tuple[str, ...]) -> dict[str, str]:
    if not ENV_FILE.is_file():
        raise LiveProofError("env_file_missing")
    # Source the authoritative shell env, but print only the explicitly requested
    # values through NUL separators. Nothing is written to logs.
    fmt = "".join(f"%s\\0" for _ in names)
    values = " ".join(f'"${{{name}:-}}"' for name in names)
    command = f'set -a; . "$1"; set +a; printf \'{fmt}\' {values}'
    completed = subprocess.run(
        ["/usr/bin/bash", "-c", command, "bash", str(ENV_FILE)],
        check=False,
        capture_output=True,
        timeout=20,
    )
    if completed.returncode != 0:
        raise LiveProofError("env_source_failed")
    parts = completed.stdout.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) != len(names):
        raise LiveProofError("env_source_shape_invalid")
    return {name: parts[index].decode("utf-8") for index, name in enumerate(names)}


def replace_env_value(text: str, key: str, value: str) -> str:
    """Replace all active assignments with one canonical shell-safe assignment."""
    assignment = f"{key}={shlex.quote(value)}"
    output: list[str] = []
    written = False
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for raw in text.splitlines():
        if pattern.match(raw) and not raw.lstrip().startswith("#"):
            if not written:
                output.append(assignment)
                written = True
            continue
        output.append(raw)
    if not written:
        output.append(assignment)
    return "\n".join(output) + "\n"


def _vk_confirmation_code(token: str, group_id: int, api_version: str) -> str:
    body = urllib.parse.urlencode(
        {
            "group_id": group_id,
            "access_token": token,
            "v": api_version,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{VK_API_BASE}/groups.getCallbackConfirmationCode",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise LiveProofError(f"vk_confirmation_lookup_failed:{type(exc).__name__}") from exc
    if not isinstance(payload, dict) or isinstance(payload.get("error"), dict):
        raise LiveProofError("vk_confirmation_lookup_rejected")
    response_obj = payload.get("response")
    if not isinstance(response_obj, dict):
        raise LiveProofError("vk_confirmation_response_invalid")
    code = str(response_obj.get("code") or "").strip()
    if not code or len(code) > 256 or any(ch in code for ch in "\r\n\0"):
        raise LiveProofError("vk_confirmation_code_invalid")
    return code


def _wait_url(url: str, *, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        completed = _run(
            ["/usr/bin/curl", "-fsS", "--max-time", "5", url],
            check=False,
            timeout=10,
        )
        if completed.returncode == 0:
            return
        time.sleep(2)
    raise LiveProofError(f"health_timeout:{url}")


def _restart_release(release_dir: str) -> None:
    _run(["/usr/bin/bash", str(WRITE_GUARD), "for-release", release_dir], timeout=120)
    _run(["/usr/bin/systemctl", "restart", SERVICE_NAME], timeout=120)
    _wait_url(LOCAL_HEALTH_URL)
    _wait_url(LOCAL_READY_URL)
    _wait_url(PUBLIC_HEALTH_URL)


def _repair_vk_confirmation() -> str:
    names = (
        "VK_GROUP_TOKEN",
        "VK_GROUP_ID",
        "VK_CONFIRMATION_TOKEN",
        "VK_API_VERSION",
    )
    env = _load_selected_env(names)
    if not env["VK_GROUP_TOKEN"] or not env["VK_GROUP_ID"]:
        raise LiveProofError("vk_config_missing")
    try:
        group_id = int(env["VK_GROUP_ID"])
    except ValueError as exc:
        raise LiveProofError("vk_group_id_invalid") from exc
    if group_id <= 0:
        raise LiveProofError("vk_group_id_invalid")
    api_version = env["VK_API_VERSION"] or "5.199"
    provider_code = _vk_confirmation_code(env["VK_GROUP_TOKEN"], group_id, api_version)
    changed = not hmac.compare_digest(provider_code, env["VK_CONFIRMATION_TOKEN"])
    original = ENV_FILE.read_bytes()
    metadata = ENV_FILE.stat()
    if ENV_FILE.is_symlink() or metadata.st_mode & 0o002:
        raise LiveProofError("env_file_permissions_unsafe")

    if changed:
        text = original.decode("utf-8")
        updated = replace_env_value(text, "VK_CONFIRMATION_TOKEN", provider_code).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=f".{ENV_FILE.name}.vk-confirmation.", dir=ENV_FILE.parent)
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, metadata.st_mode & 0o777)
            try:
                os.chown(temp, metadata.st_uid, metadata.st_gid)
            except PermissionError:
                pass
            os.replace(temp, ENV_FILE)
        finally:
            temp.unlink(missing_ok=True)

    try:
        current = _inspect(CURRENT_LINK)
        _restart_release(current["path"])
        audit_env = os.environ.copy()
        # Re-source the authoritative env in a shell so the audit sees the new value.
        audit = subprocess.run(
            [
                "/usr/bin/bash",
                "-c",
                'set -a; . "$1"; set +a; exec "$2" "$3"',
                "bash",
                str(ENV_FILE),
                current["python"],
                str(VK_AUDIT),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=audit_env,
        )
        if audit.returncode != 0 or not audit.stdout.startswith("status=ok "):
            raise LiveProofError("vk_post_repair_audit_failed")
    except Exception:
        if changed:
            ENV_FILE.write_bytes(original)
            os.chmod(ENV_FILE, metadata.st_mode & 0o777)
            try:
                os.chown(ENV_FILE, metadata.st_uid, metadata.st_gid)
            except PermissionError:
                pass
            try:
                current = _inspect(CURRENT_LINK)
                _restart_release(current["path"])
            except Exception:
                pass
        raise
    return f"vk=ok group={group_id} confirmation=match changed={int(changed)}"


def _inspect(link: Path) -> dict[str, Any]:
    completed = _run(
        [
            "/usr/bin/python3",
            str(IMMUTABLE_RELEASE),
            "inspect",
            str(link),
            "--required",
        ],
        timeout=180,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise LiveProofError("release_inspect_invalid")
    return value


def _validate_release(path: str) -> dict[str, Any]:
    completed = _run(
        ["/usr/bin/python3", str(IMMUTABLE_RELEASE), "validate", path],
        timeout=300,
    )
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise LiveProofError("release_validate_invalid")
    return value


def _verify_systemd_current() -> None:
    working = _run(
        ["/usr/bin/systemctl", "show", SERVICE_NAME, "--property=WorkingDirectory", "--value"],
        timeout=30,
    ).stdout.strip()
    exec_start = _run(
        ["/usr/bin/systemctl", "show", SERVICE_NAME, "--property=ExecStart", "--value"],
        timeout=30,
    ).stdout.strip()
    if working != str(CURRENT_LINK):
        raise LiveProofError("systemd_working_directory_not_current")
    if f"{CURRENT_LINK}/.venv/bin/python" not in exec_start or f"{CURRENT_LINK}/main.py" not in exec_start:
        raise LiveProofError("systemd_execstart_not_current")


def _verify_deployment_proof(current: dict[str, Any], previous: dict[str, Any]) -> None:
    if not DEPLOYMENT_PROOF_FILE.is_file() or not DEPLOYED_SHA_FILE.is_file():
        raise LiveProofError("deployment_proof_missing")
    proof = json.loads(DEPLOYMENT_PROOF_FILE.read_text(encoding="utf-8"))
    deployed_sha = DEPLOYED_SHA_FILE.read_text(encoding="utf-8").strip()
    if deployed_sha != current["sha"]:
        raise LiveProofError("deployed_sha_mismatch")
    if proof.get("deployed_sha") != current["sha"]:
        raise LiveProofError("proof_current_sha_mismatch")
    if proof.get("previous_sha") != previous["sha"]:
        raise LiveProofError("proof_previous_sha_mismatch")
    if proof.get("production_gate") != "PRODUCTION_GATE_OK":
        raise LiveProofError("production_gate_not_proved")
    if proof.get("release_tree_sha256") != current["tree_sha256"]:
        raise LiveProofError("proof_current_tree_mismatch")
    if proof.get("previous_release_tree_sha256") != previous["tree_sha256"]:
        raise LiveProofError("proof_previous_tree_mismatch")


def _rollback_live_proof() -> str:
    _verify_systemd_current()
    before_current = _inspect(CURRENT_LINK)
    before_previous = _inspect(PREVIOUS_LINK)
    if before_current["sha"] == before_previous["sha"]:
        raise LiveProofError("rollback_targets_not_distinct")
    _verify_deployment_proof(before_current, before_previous)
    _validate_release(before_current["path"])
    _validate_release(before_previous["path"])
    restored = False
    try:
        _run(
            [
                "/usr/bin/python3",
                str(IMMUTABLE_RELEASE),
                "rollback",
                "--current-link",
                str(CURRENT_LINK),
                "--previous-link",
                str(PREVIOUS_LINK),
            ],
            timeout=180,
        )
        rolled_current = _inspect(CURRENT_LINK)
        if rolled_current["sha"] != before_previous["sha"]:
            raise LiveProofError("rollback_target_mismatch")
        _restart_release(rolled_current["path"])
        _validate_release(before_current["path"])
        _validate_release(before_previous["path"])

        _run(
            [
                "/usr/bin/python3",
                str(IMMUTABLE_RELEASE),
                "switch",
                "--release-dir",
                before_current["path"],
                "--current-link",
                str(CURRENT_LINK),
                "--previous-link",
                str(PREVIOUS_LINK),
            ],
            timeout=180,
        )
        _restart_release(before_current["path"])
        restored = True
    finally:
        try:
            now = _inspect(CURRENT_LINK)
        except Exception:
            now = {}
        if now.get("sha") != before_current["sha"]:
            try:
                _run(
                    [
                        "/usr/bin/python3",
                        str(IMMUTABLE_RELEASE),
                        "switch",
                        "--release-dir",
                        before_current["path"],
                        "--current-link",
                        str(CURRENT_LINK),
                        "--previous-link",
                        str(PREVIOUS_LINK),
                    ],
                    timeout=180,
                )
                _restart_release(before_current["path"])
                restored = True
            except Exception:
                restored = False
        if not restored:
            raise LiveProofError("rollback_restore_failed")

    after_current = _inspect(CURRENT_LINK)
    after_previous = _inspect(PREVIOUS_LINK)
    if after_current["sha"] != before_current["sha"] or after_previous["sha"] != before_previous["sha"]:
        raise LiveProofError("rollback_topology_not_restored")
    if after_current["tree_sha256"] != before_current["tree_sha256"]:
        raise LiveProofError("current_tree_changed_during_rollback")
    if after_previous["tree_sha256"] != before_previous["tree_sha256"]:
        raise LiveProofError("previous_tree_changed_during_rollback")
    _validate_release(after_current["path"])
    _validate_release(after_previous["path"])
    _verify_systemd_current()
    _verify_deployment_proof(after_current, after_previous)
    return (
        "rollback=ok hashes=preserved systemd=current production_gate=ok "
        f"deployed={after_current['sha']} previous={after_previous['sha']}"
    )


def main() -> int:
    message = _trigger_message()
    want_vk = "[vk-confirmation-sync-request]" in message
    want_rollback = "[rollback-live-proof-request]" in message
    if not want_vk and not want_rollback:
        return 0

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        details: list[str] = []
        try:
            if want_vk:
                details.append(_repair_vk_confirmation())
            if want_rollback:
                details.append(_rollback_live_proof())
            result = (
                f"[ops-live-proof-result] trigger={TRIGGER_SHA[:12]} status=ok "
                + " ".join(details)
            )
            _publish_result(result)
            print(result)
            return 0
        except Exception as exc:
            error = _safe_fragment(f"{type(exc).__name__}:{exc}")
            result = (
                f"[ops-live-proof-result] trigger={TRIGGER_SHA[:12]} "
                f"status=error error={error}"
            )
            try:
                _publish_result(result)
            except Exception:
                pass
            print(result)
            return 1
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    raise SystemExit(main())
