from __future__ import annotations

"""Explicit, trigger-bound production live proofs.

The runner is inert unless the exact deploy trigger commit carries one of the
allowlisted request markers. It never prints credentials. The supported proofs
repair a stale VK Callback API confirmation value and exercise a real immutable
current/previous rollback followed by restoration of the exact original release.
"""

import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
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
_SAFE_RESULT_RE = re.compile(r"[^A-Za-z0-9_.:=,/\[\] -]+")
VK_API_BASE = "https://api.vk.com/method"


class LiveProofError(RuntimeError):
    """Expected operational failure that is safe to publish without secrets."""


def _run(
    args: list[str],
    *,
    check: bool = True,
    timeout: int = 120,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            input=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        raise LiveProofError(f"command_timeout:{Path(args[0]).name}") from exc
    except OSError as exc:
        raise LiveProofError(f"command_os_error:{Path(args[0]).name}") from exc
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "command failed").strip()
        raise LiveProofError(
            f"command_failed:{Path(args[0]).name}:{completed.returncode}:{detail[:180]}"
        )
    return completed


def _run_bytes(args: list[str], *, timeout: int = 30) -> bytes:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise LiveProofError(f"command_timeout:{Path(args[0]).name}") from exc
    except OSError as exc:
        raise LiveProofError(f"command_os_error:{Path(args[0]).name}") from exc
    if completed.returncode != 0:
        raise LiveProofError(f"command_failed:{Path(args[0]).name}:{completed.returncode}")
    return completed.stdout


def _trigger_message() -> str:
    if _SHA_RE.fullmatch(TRIGGER_SHA) is None:
        raise LiveProofError("invalid_trigger_sha")
    _run(["/usr/bin/git", "-C", str(APP_DIR), "fetch", "origin", "main"], timeout=180)
    return _run(
        ["/usr/bin/git", "-C", str(APP_DIR), "show", "-s", "--format=%B", TRIGGER_SHA],
        timeout=30,
    ).stdout


def _safe_fragment(value: object, *, limit: int = 180) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return (_SAFE_RESULT_RE.sub("_", text)[:limit] or "NONE").strip()


def _publish_result(message: str) -> None:
    safe_message = _safe_fragment(message, limit=500)
    for attempt in range(1, 4):
        _run(["/usr/bin/git", "-C", str(APP_DIR), "fetch", "origin", "main"], timeout=180)
        parent = _run(
            ["/usr/bin/git", "-C", str(APP_DIR), "rev-parse", "origin/main"], timeout=20
        ).stdout.strip()
        tree = _run(
            ["/usr/bin/git", "-C", str(APP_DIR), "rev-parse", f"{parent}^{{tree}}"],
            timeout=20,
        ).stdout.strip()
        commit = _run(
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
            timeout=20,
            input_text=safe_message + "\n",
        )
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
    fmt = "".join("%s\\0" for _ in names)
    values = " ".join(f'"${{{name}:-}}"' for name in names)
    command = f'set -a; . "$1"; set +a; printf \'{fmt}\' {values}'
    raw = _run_bytes(
        ["/usr/bin/bash", "-c", command, "bash", str(ENV_FILE)],
        timeout=20,
    )
    parts = raw.split(b"\0")
    if parts and parts[-1] == b"":
        parts.pop()
    if len(parts) != len(names):
        raise LiveProofError("env_source_shape_invalid")
    try:
        return {name: parts[index].decode("utf-8") for index, name in enumerate(names)}
    except UnicodeDecodeError as exc:
        raise LiveProofError("env_source_encoding_invalid") from exc


def replace_env_value(text: str, key: str, value: str) -> str:
    """Replace duplicate active assignments with one canonical shell-safe value."""
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
        {"group_id": group_id, "access_token": token, "v": api_version}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{VK_API_BASE}/groups.getCallbackConfirmationCode",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise LiveProofError("vk_confirmation_lookup_url_error") from exc
    except TimeoutError as exc:
        raise LiveProofError("vk_confirmation_lookup_timeout") from exc
    except OSError as exc:
        raise LiveProofError("vk_confirmation_lookup_os_error") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LiveProofError("vk_confirmation_lookup_json_invalid") from exc
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


def _read_env_bytes() -> tuple[bytes, os.stat_result]:
    try:
        if ENV_FILE.is_symlink():
            raise LiveProofError("env_file_symlink_unsafe")
        metadata = ENV_FILE.stat()
        original = ENV_FILE.read_bytes()
    except OSError as exc:
        raise LiveProofError("env_file_read_failed") from exc
    if metadata.st_mode & stat.S_IWOTH:
        raise LiveProofError("env_file_permissions_unsafe")
    return original, metadata


def _write_env_bytes(payload: bytes, metadata: os.stat_result) -> None:
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{ENV_FILE.name}.live-proof.", dir=ENV_FILE.parent
        )
    except OSError as exc:
        raise LiveProofError("env_temp_create_failed") from exc
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, stat.S_IMODE(metadata.st_mode))
        try:
            os.chown(temp, metadata.st_uid, metadata.st_gid)
        except PermissionError:
            pass
        os.replace(temp, ENV_FILE)
    except OSError as exc:
        raise LiveProofError("env_atomic_write_failed") from exc
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _audit_vk(current: dict[str, Any]) -> None:
    audit = _run(
        [
            "/usr/bin/bash",
            "-c",
            'set -a; . "$1"; set +a; exec "$2" "$3"',
            "bash",
            str(ENV_FILE),
            str(current["python"]),
            str(VK_AUDIT),
        ],
        check=False,
        timeout=60,
        env=os.environ.copy(),
    )
    if audit.returncode != 0 or not audit.stdout.startswith("status=ok "):
        raise LiveProofError("vk_post_repair_audit_failed")


def _restore_vk_env(original: bytes, metadata: os.stat_result) -> None:
    _write_env_bytes(original, metadata)
    current = _inspect(CURRENT_LINK)
    _restart_release(str(current["path"]))


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

    provider_code = _vk_confirmation_code(
        env["VK_GROUP_TOKEN"], group_id, env["VK_API_VERSION"] or "5.199"
    )
    changed = not hmac.compare_digest(provider_code, env["VK_CONFIRMATION_TOKEN"])
    original, metadata = _read_env_bytes()
    if changed:
        try:
            text = original.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LiveProofError("env_file_encoding_invalid") from exc
        updated = replace_env_value(text, "VK_CONFIRMATION_TOKEN", provider_code).encode(
            "utf-8"
        )
        _write_env_bytes(updated, metadata)

    try:
        current = _inspect(CURRENT_LINK)
        _restart_release(str(current["path"]))
        _audit_vk(current)
    except LiveProofError as proof_error:
        if changed:
            try:
                _restore_vk_env(original, metadata)
            except LiveProofError as restore_error:
                raise LiveProofError("vk_env_restore_failed") from restore_error
        raise proof_error
    return f"vk=ok group={group_id} confirmation=match changed={int(changed)}"


def _json_object(text: str, error_code: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LiveProofError(error_code) from exc
    if not isinstance(value, dict):
        raise LiveProofError(error_code)
    return value


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
    return _json_object(completed.stdout, "release_inspect_invalid")


def _validate_release(path: str) -> dict[str, Any]:
    completed = _run(
        ["/usr/bin/python3", str(IMMUTABLE_RELEASE), "validate", path],
        timeout=300,
    )
    return _json_object(completed.stdout, "release_validate_invalid")


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
    if (
        f"{CURRENT_LINK}/.venv/bin/python" not in exec_start
        or f"{CURRENT_LINK}/main.py" not in exec_start
    ):
        raise LiveProofError("systemd_execstart_not_current")


def _verify_deployment_proof(current: dict[str, Any], previous: dict[str, Any]) -> None:
    try:
        proof_text = DEPLOYMENT_PROOF_FILE.read_text(encoding="utf-8")
        deployed_sha = DEPLOYED_SHA_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise LiveProofError("deployment_proof_missing") from exc
    proof = _json_object(proof_text, "deployment_proof_invalid")
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


def _switch_to(release_dir: str) -> None:
    _run(
        [
            "/usr/bin/python3",
            str(IMMUTABLE_RELEASE),
            "switch",
            "--release-dir",
            release_dir,
            "--current-link",
            str(CURRENT_LINK),
            "--previous-link",
            str(PREVIOUS_LINK),
        ],
        timeout=180,
    )


def _restore_original_release(before_current: dict[str, Any]) -> None:
    now = _inspect(CURRENT_LINK)
    if now.get("sha") != before_current["sha"]:
        _switch_to(str(before_current["path"]))
    _restart_release(str(before_current["path"]))


def _rollback_live_proof() -> str:
    _verify_systemd_current()
    before_current = _inspect(CURRENT_LINK)
    before_previous = _inspect(PREVIOUS_LINK)
    if before_current["sha"] == before_previous["sha"]:
        raise LiveProofError("rollback_targets_not_distinct")
    _verify_deployment_proof(before_current, before_previous)
    _validate_release(str(before_current["path"]))
    _validate_release(str(before_previous["path"]))

    proof_error: LiveProofError | None = None
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
        _restart_release(str(rolled_current["path"]))
        _validate_release(str(before_current["path"]))
        _validate_release(str(before_previous["path"]))
        _switch_to(str(before_current["path"]))
        _restart_release(str(before_current["path"]))
    except LiveProofError as exc:
        proof_error = exc

    try:
        _restore_original_release(before_current)
    except LiveProofError as restore_error:
        raise LiveProofError("rollback_restore_failed") from restore_error
    if proof_error is not None:
        raise proof_error

    after_current = _inspect(CURRENT_LINK)
    after_previous = _inspect(PREVIOUS_LINK)
    if (
        after_current["sha"] != before_current["sha"]
        or after_previous["sha"] != before_previous["sha"]
    ):
        raise LiveProofError("rollback_topology_not_restored")
    if after_current["tree_sha256"] != before_current["tree_sha256"]:
        raise LiveProofError("current_tree_changed_during_rollback")
    if after_previous["tree_sha256"] != before_previous["tree_sha256"]:
        raise LiveProofError("previous_tree_changed_during_rollback")
    _validate_release(str(after_current["path"]))
    _validate_release(str(after_previous["path"]))
    _verify_systemd_current()
    _verify_deployment_proof(after_current, after_previous)
    return (
        "rollback=ok hashes=preserved systemd=current production_gate=ok "
        f"deployed={after_current['sha']} previous={after_previous['sha']}"
    )


def _execute_requested_proofs(message: str) -> str | None:
    want_vk = "[vk-confirmation-sync-request]" in message
    want_rollback = "[rollback-live-proof-request]" in message
    if not want_vk and not want_rollback:
        return None
    details: list[str] = []
    if want_vk:
        details.append(_repair_vk_confirmation())
    if want_rollback:
        details.append(_rollback_live_proof())
    return " ".join(details)


def main() -> int:
    try:
        message = _trigger_message()
        requested = _execute_requested_proofs(message)
    except LiveProofError as exc:
        error = _safe_fragment(f"{type(exc).__name__}:{exc}")
        result = (
            f"[ops-live-proof-result] trigger={TRIGGER_SHA[:12]} "
            f"status=error error={error}"
        )
        try:
            _publish_result(result)
        except LiveProofError:
            pass
        print(result)
        return 1
    if requested is None:
        return 0

    result = (
        f"[ops-live-proof-result] trigger={TRIGGER_SHA[:12]} status=ok {requested}"
    )
    try:
        _publish_result(result)
    except LiveProofError as exc:
        print(
            f"[ops-live-proof-result] trigger={TRIGGER_SHA[:12]} status=error "
            f"error={_safe_fragment(exc)}"
        )
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with LOCK_FILE.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                raise SystemExit(main())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        print(
            f"[ops-live-proof-result] trigger={TRIGGER_SHA[:12]} status=error "
            f"error={_safe_fragment(f'lock_failed:{type(exc).__name__}')}"
        )
        raise SystemExit(2) from exc
