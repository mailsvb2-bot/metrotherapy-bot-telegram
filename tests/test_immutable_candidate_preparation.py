from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREPARER = ROOT / "scripts" / "prepare_immutable_candidate.sh"
VALIDATOR = ROOT / "scripts" / "validate_project.py"


def _run(*command: str, cwd: Path) -> str:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return completed.stdout.strip()


def _fixture(tmp_path: Path) -> tuple[Path, str, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run("git", "init", "-b", "main", cwd=repo)
    _run("git", "config", "user.name", "Candidate Test", cwd=repo)
    _run("git", "config", "user.email", "candidate@example.test", cwd=repo)
    (repo / "app.txt").write_text("candidate\n", encoding="utf-8")
    _run("git", "add", "app.txt", cwd=repo)
    _run("git", "commit", "-m", "candidate", cwd=repo)
    sha = _run("git", "rev-parse", "HEAD", cwd=repo)

    runtime = tmp_path / "runtime"
    releases = runtime / "releases"
    releases.mkdir(parents=True)
    manager = tmp_path / "release_manager.py"
    manager.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "target = Path(sys.argv[-1])\n"
        "raise SystemExit(0 if (target / '.valid').is_file() else 1)\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "METRO_RUNTIME_ROOT": str(runtime),
            "METRO_RELEASES_DIR": str(releases),
            "METRO_CURRENT_RELEASE_LINK": str(runtime / "current"),
            "METRO_PREVIOUS_RELEASE_LINK": str(runtime / "previous"),
            "RELEASE_MANAGER": str(manager),
            "SYSTEM_PYTHON": sys.executable,
        }
    )
    return repo, sha, runtime, env


def _invoke(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(
        [bash, str(PREPARER), str(repo)],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_invalid_unreferenced_candidate_is_removed(tmp_path: Path) -> None:
    repo, sha, runtime, env = _fixture(tmp_path)
    target = runtime / "releases" / sha
    target.mkdir()
    (target / "mutated.pyc").write_bytes(b"changed")
    safe_current = runtime / "recovery" / "current"
    safe_current.mkdir(parents=True)
    (runtime / "current").symlink_to(safe_current)

    completed = _invoke(repo, env)

    assert completed.returncode == 0, completed.stderr
    assert not target.exists()
    assert f"INVALID_UNREFERENCED_CANDIDATE_REMOVED sha={sha}" in completed.stdout


def test_valid_candidate_is_reused(tmp_path: Path) -> None:
    repo, sha, runtime, env = _fixture(tmp_path)
    target = runtime / "releases" / sha
    target.mkdir()
    (target / ".valid").write_text("ok\n", encoding="utf-8")

    completed = _invoke(repo, env)

    assert completed.returncode == 0, completed.stderr
    assert target.is_dir()
    assert f"IMMUTABLE_CANDIDATE_REUSABLE sha={sha}" in completed.stdout


def test_referenced_invalid_candidate_is_never_removed(tmp_path: Path) -> None:
    repo, sha, runtime, env = _fixture(tmp_path)
    target = runtime / "releases" / sha
    target.mkdir()
    (runtime / "current").symlink_to(target)

    completed = _invoke(repo, env)

    assert completed.returncode == 7
    assert target.is_dir()
    assert "refusing to remove referenced invalid candidate" in completed.stderr


def test_release_validator_preserves_precompiled_tree() -> None:
    source = VALIDATOR.read_text(encoding="utf-8")
    assert "shutil.rmtree" not in source
    assert "release validation changed compiled bytecode artifacts" in source
    assert "_RELEASE_BYTECODE_SNAPSHOT" in source
    assert 'sys.dont_write_bytecode = True' in source
