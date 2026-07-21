from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy.sh"
RECOVERY = ROOT / "scripts" / "repair_contaminated_current_release.sh"
WRITE_GUARD = ROOT / "scripts" / "install_runtime_write_guard.sh"
BODY_HANDLER = ROOT / "handlers" / "mood_flow" / "body.py"


def _run(*command: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def _git(repo: Path, *args: str) -> str:
    completed = _run("git", *args, cwd=repo)
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _commit(repo: Path, name: str, payload: str) -> str:
    (repo / name).write_text(payload, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", f"commit {name}")
    return _git(repo, "rev-parse", "HEAD")


def test_runtime_write_guard_and_recovery_scripts_have_valid_bash_syntax() -> None:
    bash = shutil.which("bash")
    assert bash is not None
    for path in (DEPLOY, RECOVERY, WRITE_GUARD):
        completed = _run(bash, "-n", str(path), cwd=ROOT)
        assert completed.returncode == 0, f"{path}: {completed.stderr}"


def test_deploy_installs_read_only_runtime_guard_before_recovery() -> None:
    source = DEPLOY.read_text(encoding="utf-8")
    guard = source.index('bash "$WRITE_GUARD_SCRIPT"')
    repair = source.index('bash "$RECOVERY_SCRIPT" repair "$SOURCE_DIR"', guard)
    immutable = source.index('bash "$SOURCE_DIR/scripts/immutable_deploy.sh"', repair)
    assert guard < repair < immutable
    assert 'export PYTHONPYCACHEPREFIX="$STATE_ROOT/python-cache"' in source
    assert 'export XDG_CACHE_HOME="$STATE_ROOT/xdg-cache"' in source
    assert 'export MPLCONFIGDIR="$STATE_ROOT/matplotlib"' in source
    assert 'export TMPDIR="$STATE_ROOT/tmp"' in source
    assert '/usr/bin/systemctl restart "$SERVICE_NAME"' in source

    dropin = WRITE_GUARD.read_text(encoding="utf-8")
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in dropin
    assert "Environment=PYTHONPYCACHEPREFIX=$STATE_ROOT/python-cache" in dropin
    assert "Environment=XDG_CACHE_HOME=$STATE_ROOT/xdg-cache" in dropin
    assert "Environment=MPLCONFIGDIR=$STATE_ROOT/matplotlib" in dropin
    assert "Environment=TMPDIR=$STATE_ROOT/tmp" in dropin
    assert "ReadOnlyPaths=$RUNTIME_ROOT" in dropin


def test_mood_schedule_rollback_uses_narrow_exception_boundary() -> None:
    tree = ast.parse(BODY_HANDLER.read_text(encoding="utf-8"))
    broad: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            broad.append(node.lineno)
            continue
        names: set[str] = set()
        targets = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
        if names.intersection({"Exception", "BaseException"}):
            broad.append(node.lineno)
    assert broad == []


def test_failed_switch_is_rescued_to_recorded_previous_release(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    timeout = shutil.which("timeout")
    git = shutil.which("git")
    assert bash and timeout and git

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Recovery Test")
    _git(repo, "config", "user.email", "recovery@example.test")
    recorded_sha = _commit(repo, "recorded.txt", "recorded\n")
    failed_sha = _commit(repo, "failed.txt", "failed\n")

    runtime = tmp_path / "runtime"
    releases = runtime / "releases"
    recovery_root = runtime / "recovery-releases"
    state = tmp_path / "state"
    releases.mkdir(parents=True)
    recovery_root.mkdir(parents=True)
    state.mkdir()

    failed_release = releases / failed_sha
    failed_release.mkdir()
    recorded_release = recovery_root / "generation-1" / recorded_sha
    recorded_release.mkdir(parents=True)
    (recorded_release / ".valid").write_text("ok\n", encoding="utf-8")

    current = runtime / "current"
    previous = runtime / "previous"
    current.symlink_to(failed_release)
    previous.symlink_to(recorded_release)
    deployed_sha = state / "deployed_sha"
    deployed_sha.write_text(f"{recorded_sha}\n", encoding="utf-8")

    manager = tmp_path / "manager.py"
    manager.write_text(
        """
from __future__ import annotations
import json
import sys
from pathlib import Path

command = sys.argv[1]
path = Path(sys.argv[2]).resolve(strict=True)
if command == "validate":
    raise SystemExit(0 if (path / ".valid").is_file() else 1)
if command == "inspect":
    if not (path / ".valid").is_file():
        raise SystemExit(1)
    print(json.dumps({"path": str(path), "sha": path.name}))
    raise SystemExit(0)
raise SystemExit(2)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    builder = tmp_path / "builder.sh"
    builder.write_text("#!/usr/bin/env bash\nexit 99\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "METRO_RUNTIME_ROOT": str(runtime),
            "METRO_RELEASES_DIR": str(releases),
            "METRO_CURRENT_RELEASE_LINK": str(current),
            "METRO_PREVIOUS_RELEASE_LINK": str(previous),
            "DEPLOY_STATE_DIR": str(state),
            "DEPLOYED_SHA_FILE": str(deployed_sha),
            "METRO_RECOVERY_RELEASES_ROOT": str(recovery_root),
            "METRO_RECOVERY_STATE_DIR": str(state / "contaminated"),
            "SYSTEM_PYTHON": sys.executable,
            "RELEASE_MANAGER": str(manager),
            "RELEASE_BUILDER": str(builder),
            "TIMEOUT_BIN": timeout,
        }
    )

    completed = _run(bash, str(RECOVERY), "repair", str(repo), cwd=ROOT, env=env)
    assert completed.returncode == 0, completed.stderr
    assert "CURRENT_RELEASE_ROLLBACK_RESCUED" in completed.stdout
    assert current.resolve(strict=True) == recorded_release.resolve(strict=True)
    assert previous.resolve(strict=True) == recorded_release.resolve(strict=True)
