from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import immutable_release


def _release(root: Path, sha: str, *, payload: str) -> Path:
    release = root / sha
    python = release / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    (release / "main.py").write_text(payload, encoding="utf-8")
    lock = release / "requirements.txt"
    lock.write_text("example==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    tree_sha = immutable_release.release_tree_sha256(release)
    marker = {
        "sha": sha,
        "built_at_utc": "2026-07-17T20:00:00+00:00",
        "lock_file": "requirements.txt",
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "tree_sha256": tree_sha,
    }
    (release / ".release.json").write_text(
        json.dumps(marker, sort_keys=True),
        encoding="utf-8",
    )
    return release


def test_switch_and_rollback_use_only_atomic_links(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first = _release(releases, "1" * 40, payload="first")
    second = _release(releases, "2" * 40, payload="second")
    current = tmp_path / "current"
    previous = tmp_path / "previous"

    initial = immutable_release.switch_release(
        release_dir=first,
        current_link=current,
        previous_link=previous,
    )
    assert initial.current.sha == "1" * 40
    assert initial.previous is None
    assert current.resolve() == first.resolve()
    assert not previous.exists()

    upgraded = immutable_release.switch_release(
        release_dir=second,
        current_link=current,
        previous_link=previous,
    )
    assert upgraded.current.sha == "2" * 40
    assert upgraded.previous is not None
    assert upgraded.previous.sha == "1" * 40
    assert current.resolve() == second.resolve()
    assert previous.resolve() == first.resolve()

    rolled_back = immutable_release.rollback_release(
        current_link=current,
        previous_link=previous,
    )
    assert rolled_back.current.sha == "1" * 40
    assert rolled_back.previous is not None
    assert rolled_back.previous.sha == "2" * 40
    assert current.resolve() == first.resolve()
    assert previous.resolve() == second.resolve()
    assert (current / "main.py").read_text(encoding="utf-8") == "first"


def test_release_tree_mutation_is_rejected(tmp_path: Path) -> None:
    release = _release(tmp_path, "3" * 40, payload="sealed")
    immutable_release.validate_release(release)

    (release / "main.py").write_text("mutated", encoding="utf-8")

    with pytest.raises(ValueError, match="tree changed"):
        immutable_release.validate_release(release)


def test_dependency_lock_mutation_is_rejected(tmp_path: Path) -> None:
    release = _release(tmp_path, "4" * 40, payload="sealed")
    (release / "requirements.txt").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="lock content changed"):
        immutable_release.validate_release(release)


def test_deployment_proof_is_atomic_and_tied_to_both_releases(tmp_path: Path) -> None:
    releases = tmp_path / "releases"
    first = _release(releases, "5" * 40, payload="first")
    second = _release(releases, "6" * 40, payload="second")
    current = tmp_path / "current"
    previous = tmp_path / "previous"
    proof = tmp_path / "state" / "deployment-proof.json"

    immutable_release.switch_release(
        release_dir=first,
        current_link=current,
        previous_link=previous,
    )
    immutable_release.switch_release(
        release_dir=second,
        current_link=current,
        previous_link=previous,
    )
    payload = immutable_release.write_deployment_proof(
        proof_file=proof,
        current_link=current,
        previous_link=previous,
        production_gate="PRODUCTION_GATE_OK",
        health_url="http://127.0.0.1/healthz",
        readiness_url="http://127.0.0.1/readyz",
    )

    stored = json.loads(proof.read_text(encoding="utf-8"))
    assert stored == payload
    assert stored["deployed_sha"] == "6" * 40
    assert stored["previous_sha"] == "5" * 40
    assert stored["release_tree_sha256"] == immutable_release.validate_release(second).tree_sha256
    assert stored["previous_release_tree_sha256"] == immutable_release.validate_release(first).tree_sha256
    assert stored["production_gate"] == "PRODUCTION_GATE_OK"
    assert proof.stat().st_mode & 0o777 == 0o644


def test_invalid_or_dangling_release_link_fails_closed(tmp_path: Path) -> None:
    current = tmp_path / "current"
    current.symlink_to(tmp_path / "missing")

    with pytest.raises(ValueError, match="dangling"):
        immutable_release.resolve_link(current, required=True)
